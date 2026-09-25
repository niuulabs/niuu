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
import signal
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from stat import S_ISREG
from typing import Any

from niuu.build_info import build_info
from niuu.domain.reasoning import MODEL_EFFORTS, validate_effort
from niuu.ports.cli import CLITransport, TransportCapabilities
from skuld.agent_usage import AgentUsageTracker
from skuld.control_errors import ControlRecoveryError
from skuld.delivery_errors import DeliveryNotAcceptedError
from skuld.transports.claude_env import claude_spawn_env
from skuld.transports.claude_text_order import preceding_tool_text
from skuld.transports.mcp_config import build_claude_mcp_config
from skuld.transports.subprocess import _DEFAULT_PERMISSION_MODE
from skuld.transports.tool_shims import ensure_codex_tool_shims

logger = logging.getLogger("skuld.transport")

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))")

# Describe input capability only. Project/session instructions own objectives, planning,
# delegation and communication style; the transport must not prescribe that workflow.
# Toggle: SKULD__TMUX_STEERING_INSTRUCTIONS.
_STEERING_INPUT_INSTRUCTION = """\
This session supports additional input while work is in progress. The CLI delivers queued input \
at its next supported boundary. Follow the applicable session and repository instructions for \
objectives, planning, delegation and progress reporting; the transport does not prescribe a \
workflow."""

_PRESENT_FILE_INSTRUCTION = """\
FILE DELIVERY: when you produce a file the user should SEE or open (a report, image, PDF, diagram, \
screenshot, chart, or build artifact), run `present-file <path> [--caption "…"] [--title "…"]`. It \
surfaces the file in the user's app as a tappable card that opens in the file preview, and accepts \
ANY path, including scratch files outside the workspace (e.g. /tmp). Use it for finished \
deliverables the user would want to open — not for routine tool output or intermediate files."""

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
_WORKSPACE_TRUST_ROW_RE = re.compile(
    r"^\s*([❯>])?\s*(?:[1-9][.)]\s+)?(No, exit|Yes, I trust this folder)\s*$"
)


def _replace_text_atomically(path: Path, text: str) -> None:
    """Replace a file so that a concurrent reader sees the old or the new content.

    Writing in place truncates first, and another session reading the same
    file at that moment parses an empty document. The new content is written
    beside the file and renamed over it, which is atomic on one filesystem. A
    symlinked file is replaced at its target so the link survives.
    """
    target = path.resolve()
    descriptor, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        if target.exists():
            shutil.copymode(target, temporary)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


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
    # tmux #{pane_title}: a per-pane identity a teammate can set (OSC title). Empty/default for the
    # primary REPL; forwarded so the broker can name teammate panes by something other than the
    # single shared window ("main").
    pane_title: str = ""
    # The `--agent-name` from the pane's start command (agent-teams teammates only). This is the
    # STABLE, authoritative teammate identity — it matches the `teammate_name` on the TeammateIdle
    # hook, so it is both the display name AND the key that lets an idle/finished teammate be
    # correlated back to its pane_id (the agents registry is keyed by pane_id). Empty for the
    # primary REPL pane and any non-teammate split.
    agent_name: str = ""


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
        resume_session_id: str = "",
        turn_idle_timeout_s: float | None = None,
        turn_no_output_timeout_s: float | None = None,
        turn_max_seconds: float | None = None,
        pane_poll_interval_s: float | None = None,
        frame_interval_s: float | None = None,
        model_gateway_url: str = "",
        model_gateway_token: str = "",
        question_transcript_max_bytes: int = 1048576,
        question_result_history_limit: int = 128,
        native_text_wait_s: float = 0.1,
        native_text_poll_s: float = 0.005,
        reasoning_effort: str = "",
        effort_control_timeout_s: float = 15.0,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        self._model_gateway_url = model_gateway_url
        self._model_gateway_token = model_gateway_token
        self._reasoning_effort = (
            validate_effort(reasoning_effort, MODEL_EFFORTS.get(model, ()))
            if reasoning_effort
            else ""
        )
        self._effort_control_timeout = effort_control_timeout_s
        self._forge_session_id = session_id or "skuld-interactive"
        self._skip_permissions = skip_permissions
        self._agent_teams = agent_teams
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._raw_mcp_servers = list(mcp_servers or [])
        self._mcp_config = build_claude_mcp_config(mcp_servers or [])
        self._sdk_port = sdk_port
        # Resume-aware restart: a restart is a FRESH tmux launching ``claude --resume <id>``
        # (start() never resurrects the old tmux), so the fresh launch re-writes the CURRENT
        # broker port into the hook settings — avoiding the stale-port fragility of reviving an
        # old tmux. Empty -> a brand-new conversation. Wired from SkuldSessionConfig by the broker.
        self._resume_session_id = (resume_session_id or "").strip() or None
        # Claude's NATIVE conversation id (the ~/.claude/projects/<ws>/<uuid>.jsonl basename),
        # captured from correlated main-pane hook payloads. This — not the tmux session name —
        # is what `claude --resume` accepts; reporting the tmux name as cli_session_id made
        # every resume-aware restart silently start a FRESH conversation (2026-07-13).
        self._claude_native_session_id: str | None = None
        if self._resume_session_id:
            try:
                self._claude_native_session_id = str(uuid.UUID(self._resume_session_id))
            except ValueError:
                # Legacy launch hints sometimes contain a tmux name. It is not
                # a native conversation identity and must never be persisted as one.
                pass

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
        self._question_result_wait_s = self._float_env(
            "SKULD__TMUX_QUESTION_RESULT_WAIT_SECONDS", None, 5.0
        )
        if question_transcript_max_bytes < 1 or question_result_history_limit < 1:
            raise ValueError("Native question transcript and receipt bounds must be positive")
        self._question_transcript_max_bytes = question_transcript_max_bytes
        if native_text_wait_s <= 0 or native_text_poll_s <= 0:
            raise ValueError("Native text wait and poll intervals must be positive")
        self._native_text_wait_s = native_text_wait_s
        self._native_text_poll_s = native_text_poll_s
        # Initial-prompt fix: the REPL isn't ready to accept input the instant the
        # CLI is spawned — pasting the seed prompt into a still-booting Claude makes
        # it land mid-startup (it was being parsed as a slash command). Wait for a
        # readiness marker in the pane (bounded) before delivering the seed prompt.
        self._repl_ready_timeout_s = self._float_env(
            "SKULD__TMUX_REPL_READY_TIMEOUT_SECONDS", None, 12.0
        )
        marker_env = os.environ.get("SKULD__TMUX_REPL_READY_MARKER", "").strip()
        self._repl_ready_markers = (
            (marker_env,) if marker_env else ("? for shortcuts", "for shortcuts", "❯")
        )
        # BUG-3: hard ceiling on a single steering delivery (lock wait + paste). A prior
        # delivery that wedged the send lock, or a hung tmux subprocess, must surface as a
        # loud error the client can see — never a silent swallow of the user's message.
        self._deliver_timeout_s = self._float_env("SKULD__TMUX_DELIVER_TIMEOUT_SECONDS", None, 15.0)
        self._frame_interval_s = self._float_env(
            "SKULD__TMUX_FRAME_INTERVAL_SECONDS", frame_interval_s, 0.12
        )
        # Graceful teardown (Epic I): on stop() we SIGTERM the live ``claude`` pane process and
        # wait up to this long for it to exit BEFORE the hard ``kill-session`` fallback. The
        # graceful exit lets claude deregister its --remote-control session from claude.ai so a
        # stopped session stops showing as "connected" on the phone (kill-session SIGHUPs it
        # abruptly and it never deregisters).
        self._graceful_term_timeout_s = self._float_env(
            "SKULD__TMUX_GRACEFUL_TERM_TIMEOUT_SECONDS", None, 5.0
        )
        # Poll cadence while waiting for the SIGTERM'd pane process to exit.
        self._graceful_term_poll_s = self._float_env(
            "SKULD__TMUX_GRACEFUL_TERM_POLL_SECONDS", None, 0.1
        )
        # Stale-socket sweep (Epic I): orphaned ``{socket_dir}/*.sock`` files (and per-session
        # dirs) whose tmux server is no longer live accumulate forever. Sweep at startup and at
        # this interval. The interval gates a periodic re-sweep loop.
        self._socket_sweep_interval_s = self._float_env(
            "SKULD__TMUX_SOCKET_SWEEP_INTERVAL_SECONDS", None, 3600.0
        )
        self._socket_dir = base_socket_dir
        self._runtime_root = Path(self.workspace_dir) / ".skuld" / "tmux-interactive"
        self._sweep_task: asyncio.Task[None] | None = None
        self._emit_raw_terminal_output = self._bool_env("SKULD__TMUX_EMIT_RAW_OUTPUT", False)
        self._hook_events_enabled = self._bool_env(
            "SKULD__TMUX_HOOK_EVENTS_ENABLED",
            bool(self._sdk_port),
        )
        # Default ON with hook mode: MessageDisplay is the live channel for the agent's
        # intermediary prose (hook mode otherwise emits tools only until Stop, so everything
        # said BETWEEN tool calls never streamed). Env-off remains available if a CLI build
        # floods it.
        self._message_display_hook_enabled = self._bool_env(
            "SKULD__TMUX_MESSAGE_DISPLAY_HOOK_ENABLED",
            self._hook_events_enabled,
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

        # Append the steering/task-tracking guidance to Claude's system prompt (on by default) so a
        # steerable session keeps a live TodoWrite plan the client can surface + steer against.
        self._steering_instructions_enabled = self._bool_env(
            "SKULD__TMUX_STEERING_INSTRUCTIONS", True
        )

        self._alive = False
        self._startup_ready = False
        self._workspace_trust_navigation_sent = False
        self._workspace_trust_submitted = False
        self._initial_prompt_sent = False
        self._lifecycle_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        # In-flight post-Enter submit-confirm loops (fire-and-forget; cancelled on stop()).
        self._confirm_submit_tasks: set[asyncio.Task[None]] = set()
        # Assistant texts already emitted via MessageDisplay this turn (re-display dedup +
        # the Stop-hook final-message twin guard) + per-message flush accumulators.
        self._turn_displayed_texts: list[str] = []
        self._display_msg_buffers: dict[str, str] = {}
        self._text_hook_lock = asyncio.Lock()
        self._displayed_message_ids: set[str] = set()
        self._native_pretool_text_ids: set[tuple[str, int]] = set()
        self._pending_native_display_texts: list[str] = []
        self._unmatched_display_texts: list[str] = []
        # A native idle notification can follow a completed MessageDisplay without
        # a Stop callback when queued input overlaps a teammate's final turn.
        self._idle_display: tuple[str, str] | None = None
        self._idle_completed_prompt = ""
        self._main_hook_tools: set[str] = set()
        # Correlation FIFO of (msg_id, request_id, normalized_text) for each user
        # message pasted into the pane but not yet seen consumed by Claude. A steered
        # message lands in the CLI's own input queue and is inserted "at the right
        # moment"; the UserPromptSubmit hook is the genuine "Claude took this prompt
        # into its flow" signal. We pop the matching entry there and stamp its msg_id
        # onto terminal_prompt_submitted so the client can flip THAT specific steering
        # bubble from "pending" to "active". Bounded so an unmatched entry can't leak.
        self._pending_prompt_correlations: deque[tuple[str | None, str | None, str]] = deque(
            maxlen=32
        )
        # Agents in flight, keyed by a resolved id (tool_use_id for Task-tool
        # subagents, subagent_id for SubagentStart, etc.). Lets the matching
        # PostToolUse / SubagentStop emit the "stopped" agent_update, and lets the
        # Task and Subagent* signals for the same agent merge instead of duplicate.
        self._hook_agents: dict[str, dict[str, Any]] = {}
        # Per-subagent token accounting. SubagentStart hands us the subagent's
        # transcript_path; the tracker tails that JSONL incrementally so GET /api/agents can
        # report a live cumulative token total per agent. Deliberately NOT stored on the agent
        # dict — that dict is emitted verbatim to clients, and the on-disk path is broker-local
        # detail, not wire contract.
        self._agent_usage = AgentUsageTracker()
        # Ordered stack of in-flight Task-subagent ids (the Task tool_use_id). A Task tool
        # BLOCKS its parent, so every NON-Task tool hook between a Task PreToolUse and that
        # Task's PostToolUse belongs to the subagent on top. Push in
        # _surface_agent_started_from_task, pop in _emit_tool_result_from_hook. Single-subagent
        # is exact (LIFO top); parallel Task calls are a KNOWN GAP (flat hooks can't disambiguate).
        self._active_subagent_stack: list[str] = []
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
        self._answer_lock = asyncio.Lock()
        self._question_result_emit_lock = asyncio.Lock()
        self._question_result_ids: deque[str] = deque(maxlen=question_result_history_limit)
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
            set_effort=True,
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
        # A resumed conversation is known before its first prompt. Fresh sessions
        # stay unknown until a correlated native hook proves the identity; the
        # tmux process name cannot be passed to Claude's resume command.
        return self._claude_native_session_id

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
        # Monitor the first page even when the session has no seed prompt.
        # Share the input lock with chat/discovery so startup keys cannot interleave.
        async with self._send_lock:
            if not self._startup_ready:
                ready = await self._wait_for_repl_ready(allow_pending_question=True)
                if not ready:
                    # Let the client finish attaching so it can answer the existing
                    # hook-backed question. A seed remains unsent until startup retries.
                    return

        if self._initial_prompt and not self._initial_prompt_sent:
            self._initial_prompt_sent = True
            try:
                await self.send_message(self._initial_prompt)
            except Exception:
                self._initial_prompt_sent = False
                raise

    async def _capture_pane_text(self, pane_id: str | None = None) -> str:
        target = self._target_pane(pane_id)
        try:
            result = await self._run_tmux("capture-pane", "-t", target, "-p")
        except Exception:  # pragma: no cover - capture is best-effort
            return ""
        return result.stdout or ""

    def _repl_looks_ready(self, text: str) -> bool:
        if self._workspace_trust_pending(text):
            return False
        # A menu's selection chevron is not the chat composer, even before the
        # trust menu has fully rendered or when the workspace is already trusted.
        return any(
            marker and marker != "❯" and marker in text for marker in self._repl_ready_markers
        ) or (
            "❯" in self._repl_ready_markers
            and any(self._is_empty_prompt_row(row) for row in text.splitlines())
        )

    @staticmethod
    def _workspace_trust_pending(text: str) -> bool:
        return "Yes, I trust this folder" in text and "Accessing workspace:" in text

    async def _confirm_workspace_trust(self, text: str, *, pane_id: str) -> None:
        """Answer only the launch workspace's trust menu, one observed step at a time.

        Callers hold the input lock. Wait for the highlight to move to Yes before
        pressing Enter; never replay either key if tmux captures the old frame.
        """
        rows = self._normalize_terminal_rows(text)
        workspace = re.search(r"(?m)^\s*Accessing workspace:\s*\n\s*(/[^\n]+)", text)
        if workspace is None:
            return  # The startup screen may still be rendering.
        displayed_path = Path(workspace[1].strip()).resolve()
        if displayed_path != Path(self.workspace_dir).resolve():
            raise DeliveryNotAcceptedError(
                "Workspace trust is for a different folder than the configured workspace; "
                "review it in the terminal"
            )
        choices = [match for row in rows if (match := _WORKSPACE_TRUST_ROW_RE.fullmatch(row))]
        if (
            len(choices) != 2
            or {choice[2] for choice in choices} != {"No, exit", "Yes, I trust this folder"}
            or "Enter to confirm" not in text
        ):
            return
        selected = [index for index, choice in enumerate(choices) if choice[1]]
        if len(selected) != 1 or self._workspace_trust_submitted:
            return
        yes_index = next(i for i, choice in enumerate(choices) if choice[2].startswith("Yes,"))
        if selected[0] == yes_index:
            # Mark before sending: an uncertain tmux failure must not replay Enter.
            self._workspace_trust_submitted = True
            await self._send_key("Enter", pane_id=pane_id)
            logger.info("Submitted Claude workspace trust confirmation for %s", displayed_path)
            return
        if not self._workspace_trust_navigation_sent:
            self._workspace_trust_navigation_sent = True
            await self._send_key("Down" if yes_index > selected[0] else "Up", pane_id=pane_id)

    async def _wait_for_repl_ready(self, *, allow_pending_question: bool = False) -> bool:
        """Monitor startup, confirm workspace trust, then wait for the input prompt.

        Called with the input lock held. A timeout leaves the terminal available
        for inspection and never pastes a seed/chat/discovery probe into a menu.

        ``allow_pending_question`` is for ``start()`` only: a CLI that has raised
        a native control (a resumed question, a permission menu) hides the
        composer. Waiting for the composer there deadlocks — the browser that
        would answer the control cannot attach until ``start()`` returns. It
        returns False without marking startup ready, so nothing is typed into
        the control. Callers that are about to type leave it False; they must
        see the composer itself.
        """
        target = self._target_pane()
        self._startup_ready = False
        deadline = time.monotonic() + max(self._repl_ready_timeout_s, 0.0)
        trust_seen = False
        while time.monotonic() < deadline:
            text = self._clean_terminal_text(await self._capture_pane_text(target))
            if allow_pending_question and self._pending_tty_prompts:
                return False
            if self._workspace_trust_pending(text):
                trust_seen = True
                await self._confirm_workspace_trust(text, pane_id=target)
            elif self._repl_looks_ready(text):
                self._startup_ready = True
                return True
            await asyncio.sleep(self._menu_poll_step_s)
        if trust_seen:
            raise DeliveryNotAcceptedError(
                "Workspace trust did not complete before startup timed out; check the terminal"
            )
        raise DeliveryNotAcceptedError(
            "Claude is not ready for input; check the terminal and retry"
        )

    async def _ensure_started(self) -> None:
        async with self._lifecycle_lock:
            if not self._tmux_binary_exists():
                raise RuntimeError("tmux is required for TmuxInteractiveTransport")
            self._runtime_dir.mkdir(parents=True, exist_ok=True)
            self._pane_log_dir.mkdir(parents=True, exist_ok=True)
            self._socket_path.parent.mkdir(parents=True, exist_ok=True)

            if not await self._has_session():
                await self._create_session()
                self._startup_ready = False
                self._workspace_trust_navigation_sent = False
                self._workspace_trust_submitted = False
                await self._apply_tmux_options()
            self._alive = True
            await self._refresh_panes(emit_events=True)
            if self._pane_watcher_task is None or self._pane_watcher_task.done():
                self._pane_watcher_task = asyncio.create_task(
                    self._watch_panes(),
                    name=f"tmux-pane-watch-{self._session_name}",
                )
            if self._sweep_task is None or self._sweep_task.done():
                self._sweep_task = asyncio.create_task(
                    self._run_socket_sweep_loop(),
                    name=f"tmux-socket-sweep-{self._session_name}",
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
            for task in list(self._confirm_submit_tasks):
                task.cancel()
            self._confirm_submit_tasks.clear()
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
            if self._sweep_task is not None:
                self._sweep_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await self._sweep_task
                self._sweep_task = None
            # Graceful term BEFORE the hard kill so claude can deregister its remote-control
            # session from claude.ai (otherwise a stopped session lingers as "connected" on the
            # phone). Best-effort: any failure falls straight through to kill-session.
            await self._graceful_terminate_pane()
            await self._run_tmux("kill-session", "-t", self._session_name, check=False)
            # On-disk cleanup AFTER the session is gone: unlink the socket file and remove the
            # per-session runtime dir so neither leaks (731 stale sockets / ~10 leaked dirs in the
            # field). Both are best-effort and tightly guarded to the expected paths.
            self._cleanup_socket()
            self._cleanup_runtime_dir()

    async def _graceful_terminate_pane(self) -> None:
        """SIGTERM the live ``claude`` pane process and wait (bounded) for it to exit.

        Only meaningful when remote-control was enabled (that is the session that must
        deregister from claude.ai); when it wasn't, the abrupt kill-session is already clean, so
        we skip the extra step. Fully defensive: if the pid can't be discovered or signalling
        fails, we return and let the caller fall through to kill-session.
        """
        if not self._remote_control:
            return
        pid = await self._discover_pane_pid()
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return
        deadline = time.monotonic() + max(self._graceful_term_timeout_s, 0.0)
        while time.monotonic() < deadline:
            if not self._process_alive(pid):
                return
            await asyncio.sleep(max(self._graceful_term_poll_s, 0.0))

    async def _discover_pane_pid(self) -> int | None:
        """The pid of the ``claude`` process running in the session's pane, or None."""
        try:
            result = await self._run_tmux(
                "list-panes", "-t", self._session_name, "-F", "#{pane_pid}", check=False
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            candidate = line.strip()
            if candidate.isdigit():
                return int(candidate)
        return None

    @staticmethod
    def _process_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _cleanup_socket(self) -> None:
        with suppress(OSError):
            self._socket_path.unlink(missing_ok=True)

    def _cleanup_runtime_dir(self) -> None:
        """Remove the per-session runtime dir, guarded so we never rmtree something broader than
        the expected ``<workspace>/.skuld/tmux-interactive/<session_name>`` path."""
        runtime_dir = self._runtime_dir
        expected = self._runtime_root / self._session_name
        if runtime_dir != expected:
            return
        if runtime_dir.parent.name != "tmux-interactive":
            return
        shutil.rmtree(runtime_dir, ignore_errors=True)

    async def _run_socket_sweep_loop(self) -> None:
        """Sweep orphaned sockets at startup, then re-sweep every interval.

        Always runs against THIS transport's own socket dir / runtime root (never a broader
        path), so a long-lived broker reaps the slow drip of stale sockets a crashed/killed
        session leaves behind. Best-effort: a sweep failure never tears the session down.
        """
        try:
            while True:
                with suppress(Exception):
                    await self.sweep_stale_sockets(
                        self._socket_dir, runtime_root=self._runtime_root
                    )
                interval = max(self._socket_sweep_interval_s, 0.0)
                if interval <= 0:
                    return
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.warning("Tmux socket sweep loop failed", exc_info=True)

    async def sweep_stale_sockets(
        self, socket_dir: Path, *, runtime_root: Path | None = None
    ) -> list[Path]:
        """Remove orphaned ``{socket_dir}/*.sock`` files whose tmux server is NOT live.

        A socket is LIVE iff ``tmux -S <sock> has-session`` (run via _run_tmux against that
        socket) succeeds; if it fails/errors the server is dead and the socket is unlinked.
        Optionally removes per-session runtime dirs under ``runtime_root`` that have no live
        socket. Pure with respect to its arguments: tests pass a TEMP dir so the real
        ``/tmp/skuld-tmux-*`` is never touched.

        Returns the list of paths removed (for assertions/observability).
        """
        removed: list[Path] = []
        socket_dir = Path(socket_dir)
        if not socket_dir.is_dir():
            return removed
        live_stems: set[str] = set()
        for sock in sorted(socket_dir.glob("*.sock")):
            if await self._socket_server_is_live(sock):
                live_stems.add(sock.stem)
                continue
            with suppress(OSError):
                sock.unlink(missing_ok=True)
                removed.append(sock)
        if runtime_root is None or not Path(runtime_root).is_dir():
            return removed
        for session_dir in sorted(Path(runtime_root).iterdir()):
            if not session_dir.is_dir():
                continue
            if session_dir.name in live_stems:
                continue
            shutil.rmtree(session_dir, ignore_errors=True)
            removed.append(session_dir)
        return removed

    async def _socket_server_is_live(self, socket_path: Path) -> bool:
        """True iff a tmux server is answering on ``socket_path`` (``has-session`` succeeds)."""
        cmd = [self._tmux_bin(), "-S", str(socket_path), "has-session"]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            returncode = await process.wait()
        except Exception:
            return False
        return returncode == 0

    async def send_message(
        self, content: str, *, msg_id: str | None = None, request_id: str | None = None
    ) -> None:
        """Deliver a user message by typing it into the live interactive CLI.

        This is real steering: we never stop/restart the turn. The CLI queues
        the keystrokes and inserts them at the right moment (mid-turn it steers
        the running turn; idle it starts a fresh one). We do NOT block waiting
        for the turn to finish — the response streams back asynchronously via
        the pane tail / Claude hooks, and completion is detected by the
        watchdog or the Stop hook.

        ``msg_id``/``request_id`` (when supplied by the broker) are recorded so the
        matching UserPromptSubmit hook can flip that message from pending to active.
        """
        await self._deliver_user_text(content, msg_id=msg_id, request_id=request_id)

    async def _deliver_user_text(
        self, content: str, *, msg_id: str | None = None, request_id: str | None = None
    ) -> None:
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
            raise DeliveryNotAcceptedError(
                "steering message not delivered: the session input channel has been "
                f"busy for >{self._deliver_timeout_s:.0f}s (prior delivery stuck)"
            ) from exc
        try:
            if not self.is_alive:
                await self._ensure_started()
            if self._pending_tty_prompts:
                raise DeliveryNotAcceptedError(
                    "Claude is waiting on a native control; answer it before "
                    "sending another message"
                )
            if not self._startup_ready or self._workspace_trust_pending(
                await self._capture_pane_text()
            ):
                await self._wait_for_repl_ready()
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
        # Record the correlation only on a successful paste — the message is now in
        # the CLI's input queue. The matching UserPromptSubmit hook pops this to stamp
        # the originating msg_id onto the "Claude consumed it" signal.
        self._pending_prompt_correlations.append(
            (msg_id, request_id, self._normalize_prompt(content))
        )
        logger.info("tmux deliver: pasted %d chars into pane OK", len(content))

    @staticmethod
    def _normalize_prompt(text: str) -> str:
        """Collapse whitespace so a delivered message matches the prompt Claude echoes
        back via UserPromptSubmit (the REPL may reflow/trim it)."""
        return " ".join(text.split())

    def _match_prompt_correlation(self, prompt: str) -> tuple[str | None, str | None]:
        """Pop the delivered-message correlation whose text matches this submitted
        prompt and return its (msg_id, request_id). Returns (None, None) without
        consuming anything when there's no match (a prompt we didn't originate, e.g.
        a slash command typed directly in the pane), so ids are never mis-attributed."""
        target = self._normalize_prompt(prompt)
        if not target:
            return None, None
        for i, (msg_id, request_id, norm) in enumerate(self._pending_prompt_correlations):
            if norm == target:
                del self._pending_prompt_correlations[i]
                return msg_id, request_id
        return None, None

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
        self._turn_displayed_texts = []
        self._display_msg_buffers = {}
        self._reset_text_identity_tracking()
        self._idle_display = None
        self._idle_completed_prompt = ""
        self._main_hook_tools.clear()
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

    async def get_effort(self) -> dict:
        return {
            "current": self._reasoning_effort,
            "levels": list(MODEL_EFFORTS.get(self._model, ())),
            "mutable": True,
            "applies_to": "next_turn",
        }

    async def _set_effort(self, effort: str) -> None:
        effort = validate_effort(effort, MODEL_EFFORTS.get(self._model, ()))
        if self._turn_active:
            raise ValueError("Claude is working. Change /effort after this turn finishes.")
        async with self._send_lock:
            await self._wait_for_repl_ready()
            await self._send_slash_command("effort", arguments=effort)
            async with asyncio.timeout(self._effort_control_timeout):
                while True:
                    text = await self._capture_pane_text()
                    # Native CLI acknowledgement, never merely the echoed command.
                    confirmations = re.findall(
                        r"Set effort level to (low|medium|high|xhigh|max)\b", text
                    )
                    if confirmations and confirmations[-1] == effort:
                        self._reasoning_effort = effort
                        return
                    await asyncio.sleep(self._pane_poll_interval_s)

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        if not self.is_alive:
            await self.start()

        if subtype == "set_effort":
            await self._set_effort(str(kwargs.get("effort") or ""))
            return

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
                async with self._send_lock:
                    await self._paste_text(
                        text,
                        enter=bool(kwargs.get("enter", False)),
                        pane_id=self._coerce_str(kwargs.get("pane_id")),
                    )
            return

        if subtype in {"terminal_key", "key"}:
            raw_keys = kwargs.get("keys")
            # The broker supplies keys=[] for the single-key wire form. Only a
            # nonempty sequence overrides key; otherwise Enter/Escape silently
            # disappear between the broker and this adapter.
            if isinstance(raw_keys, list) and raw_keys:
                keys = [self._normalize_key(str(key)) for key in raw_keys if str(key)]
            else:
                key = self._coerce_str(kwargs.get("key")) or self._coerce_str(raw_keys)
                keys = [self._normalize_key(key)] if key else []
            async with self._send_lock:
                for key in keys:
                    await self._send_key(key, pane_id=self._coerce_str(kwargs.get("pane_id")))
            return

        if subtype == "terminal_resize":
            cols = int(kwargs.get("cols") or kwargs.get("columns") or 0)
            rows = int(kwargs.get("rows") or 0)
            if cols > 0 and rows > 0:
                async with self._send_lock:
                    await self._resize_pane(
                        cols=cols,
                        rows=rows,
                        pane_id=self._coerce_str(kwargs.get("pane_id")),
                    )
            return

        if subtype in {"slash_command", "terminal_slash_command"}:
            command = self._coerce_str(kwargs.get("command"))
            if command:
                async with self._send_lock:
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
                await self._deliver_user_text(
                    content,
                    msg_id=self._coerce_str(kwargs.get("msg_id")) or None,
                    request_id=self._coerce_str(kwargs.get("request_id")) or None,
                )

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
            if not self._is_child_hook(payload):
                self._idle_display = None
                self._idle_completed_prompt = ""
            self._mark_semantic_turn_started()
            prompt = payload.get("prompt")
            prompt_str = prompt if isinstance(prompt, str) else ""
            # Claude just took a user prompt into its flow. Correlate it back to the
            # message we pasted so the client can flip THAT steering bubble to active.
            msg_id, request_id = self._match_prompt_correlation(prompt_str)
            # NATIVE-ID CAPTURE: a prompt that correlates to something WE pasted is provably
            # the MAIN pane's conversation (teammate/subagent claude processes share this hook
            # endpoint and POST their OWN session ids — blind capture would wire a restart to
            # resume a subagent's conversation). Uncorrelated prompts are ignored.
            if msg_id or request_id:
                native = payload.get("session_id")
                if isinstance(native, str) and native.strip():
                    self._claude_native_session_id = native.strip()
            event: dict[str, Any] = {
                "type": "terminal_prompt_submitted",
                "event_type": "claude.prompt.submitted",
                "prompt": prompt_str,
                "claude_session_id": payload.get("session_id"),
                "transcript_path": payload.get("transcript_path"),
                "metadata": {"source": "claude_hook"},
            }
            if msg_id:
                event["msg_id"] = msg_id
            if request_id:
                event["request_id"] = request_id
            await self._emit(event)
            return True

        if event_name == "PreToolUse":
            async with self._text_hook_lock:
                self._mark_semantic_turn_started()
                if not self._is_child_hook(payload):
                    self._idle_display = None
                    self._idle_completed_prompt = ""
                    if tool_id := self._coerce_str(payload.get("tool_use_id")):
                        self._main_hook_tools.add(tool_id)
                await self._emit_native_text_before_tool(payload)
                await self._emit_tool_use_from_hook(payload)
            return True

        if event_name in {"PostToolUse", "PostToolUseFailure"}:
            await self._emit_tool_result_from_hook(
                payload,
                is_error=event_name == "PostToolUseFailure",
            )
            if not self._is_child_hook(payload):
                self._main_hook_tools.discard(self._coerce_str(payload.get("tool_use_id")))
            return True

        if event_name == "PermissionRequest":
            await self._emit_permission_request_from_hook(payload)
            return True

        if event_name == "MessageDisplay":
            async with self._text_hook_lock:
                await self._emit_message_display_from_hook(payload)
            return True

        if event_name == "Notification" and payload.get("notification_type") == "idle_prompt":
            async with self._text_hook_lock:
                await self._finish_native_idle_turn(payload)
            return True

        if event_name == "SubagentStart":
            await self._surface_subagent_start(payload)
            return True

        if event_name == "SubagentStop":
            await self._surface_subagent_stop(payload)
            return True

        if event_name == "TeammateIdle":
            # An agent-teams teammate finished its current work and went idle. Its split pane stays
            # ALIVE (idling for the next message), so neither #{pane_dead} eviction nor the vanished
            # sweep will ever reap it — this hook is the ONLY "teammate finished" signal, and it was
            # previously dropped. Evict the teammate so a running/reconnecting session shows only
            # CURRENTLY active teammates, never idle corpses lifted on replay.
            await self._finish_teammate_from_idle(payload)
            return True

        if event_name == "Stop":
            if self._is_child_hook(payload):
                return True
            if (
                not self._turn_active
                and self._idle_completed_prompt
                and payload.get("prompt_id") == self._idle_completed_prompt
                and self._coerce_str(payload.get("last_assistant_message")).strip()
                == (self._last_result or {}).get("result")
            ):
                return True
            await self._finish_hook_turn(
                content=self._coerce_str(payload.get("last_assistant_message")),
                reason="stop",
            )
            return True

        if event_name == "StopFailure":
            if self._is_child_hook(payload):
                return True
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

    def _is_child_hook(self, payload: dict[str, Any]) -> bool:
        if payload.get("is_sidechain") or payload.get("isSidechain"):
            return True
        if payload.get("agent_id") or payload.get("agentId"):
            return True
        native = self._coerce_str(payload.get("session_id"))
        return bool(
            native and self._claude_native_session_id and native != self._claude_native_session_id
        )

    def _mark_semantic_turn_started(self) -> None:
        if self._turn_active:
            return
        # Native work can start without another paste (teammates, queued input,
        # terminal input). Every new turn needs its own completion event/watchdog.
        self._begin_turn()
        self._turn_last_output_at = self._turn_started_at

    async def _finish_native_idle_turn(self, payload: dict[str, Any]) -> None:
        """Recover a missing Stop only from matching native completion evidence."""
        prompt_id = self._coerce_str(payload.get("prompt_id"))
        if (
            self._is_child_hook(payload)
            or not self._turn_active
            or not prompt_id
            or not self._idle_display
            or self._idle_display[0] != prompt_id
            or self._display_msg_buffers
            or self._main_hook_tools
            or self._pending_tty_prompts
            or self._pending_prompt_correlations
        ):
            return
        await self._finish_hook_turn(content=self._idle_display[1], reason="native_idle")
        self._idle_completed_prompt = prompt_id

    def _reset_text_identity_tracking(self) -> None:
        self._displayed_message_ids.clear()
        self._native_pretool_text_ids.clear()
        self._pending_native_display_texts.clear()
        self._unmatched_display_texts.clear()

    async def _emit_native_text_before_tool(self, payload: dict[str, Any]) -> None:
        if self._is_child_hook(payload) or (
            self._active_subagent_stack and payload.get("tool_name") != "Task"
        ):
            return
        anchor = self._question_native_identity(payload)
        if not anchor.get("transcript_path"):
            return
        # Claude can POST PreToolUse before its asynchronous JSONL write is
        # visible. Wait only for the exact native tool proof, bounded by config;
        # no text (or a writer depending on hook return) falls back to captured
        # hook order without blocking the native tool indefinitely.
        try:
            async with asyncio.timeout(self._native_text_wait_s):
                while True:
                    items = await asyncio.to_thread(preceding_tool_text, anchor)
                    if items is not None:
                        break
                    await asyncio.sleep(self._native_text_poll_s)
        except TimeoutError:
            return
        for item in items:
            identity = (item["row_id"], item["index"])
            if identity in self._native_pretool_text_ids:
                continue
            text = item["text"]
            display_text = text.strip()
            if display_text in self._unmatched_display_texts:
                self._unmatched_display_texts.remove(display_text)
                self._native_pretool_text_ids.add(identity)
                continue
            await self._emit(
                {
                    "type": "assistant",
                    "message": {
                        "model": self._model or "interactive",
                        "content": [{"type": "text", "text": text}],
                    },
                    "metadata": {
                        "source": "claude_native_pretool",
                        "claude_message_id": item["message_id"],
                        "native_row_id": item["row_id"],
                        "native_block_index": item["index"],
                        "claude_session_id": anchor["native_session_id"],
                        "before_tool_use_id": anchor["native_tool_use_id"],
                    },
                }
            )
            self._native_pretool_text_ids.add(identity)
            self._pending_native_display_texts.append(display_text)
            self._turn_displayed_texts.append(display_text)

    async def _emit_message_display_from_hook(self, payload: dict[str, Any]) -> None:
        """MessageDisplay hook → a whole-message assistant frame per displayed message, MID-TURN.

        This is the live channel for the agent's intermediary prose. Hook mode otherwise
        emits tools only (Pre/PostToolUse) plus ONE final message at Stop, so everything the
        agent says BETWEEN tool calls never streamed — clients saw tool cards appear while
        the interleaved "normal messages" were dropped until the durable rebuild.

        Wire shape (CLI ≥2.1.x): the hook fires PER FLUSH with ``turn_id``, ``message_id``
        (stable across every flush of one message), ``index`` (0-based flush counter),
        ``final`` (true exactly once, on the last flush) and ``delta`` (the newly completed
        lines). Flushes are ACCUMULATED per message_id and ONE whole-message ``assistant``
        frame is emitted on the final flush. Whole messages (not text deltas) on purpose:
        the shared reducer folds an assistant frame's text into the durable turn's typed
        ``parts`` — interleaved with the tool parts — whereas a text delta only grows the
        flat ``content`` string and would VANISH from the durable interleaved transcript."""
        # Sidechain (subagent) prose has no top-level anchor in the main flow — skip it
        # (subagent tool frames already nest via parent attribution; prose attribution is
        # a separate feature).
        if self._is_child_hook(payload):
            return
        delta = payload.get("delta")
        if not isinstance(delta, str) or not delta:
            return
        message_id = self._coerce_str(payload.get("message_id")) or "current"
        buffer = self._display_msg_buffers.get(message_id, "")
        if buffer and not buffer.endswith("\n") and not delta.startswith("\n"):
            buffer += "\n"
        buffer += delta
        if not payload.get("final"):
            self._display_msg_buffers[message_id] = buffer
            if self._turn_active:
                # Keep the watchdog fed while a long message is still flushing.
                self._turn_last_output_at = time.monotonic()
            return
        self._display_msg_buffers.pop(message_id, None)
        text = buffer.strip()
        if not text:
            return
        # LATE-FLUSH RACE (verified live): on a fast turn cycle the final display flush can
        # land AFTER the Stop hook. The result path already carried this text into the turn
        # (apply_result_content), so re-emitting it would fabricate a twin turn — and calling
        # _mark_semantic_turn_started here would open a PHANTOM turn. Drop the echo; only a
        # genuinely novel post-turn message (never carried by the last result) still emits.
        if not self._turn_active:
            last_result_text = str((self._last_result or {}).get("result") or "")
            if self._normalize_prompt(text) == self._normalize_prompt(last_result_text):
                return
        self._mark_semantic_turn_started()
        prompt_id = self._coerce_str(payload.get("prompt_id"))
        if prompt_id:
            self._idle_display = (prompt_id, text)
            self._idle_completed_prompt = ""
        # Native and display IDs differ. Consume only ONE exact delayed display
        # occurrence per pre-tool native item; identical later messages survive.
        if message_id != "current" and message_id in self._displayed_message_ids:
            return
        self._displayed_message_ids.add(message_id)
        if text in self._pending_native_display_texts:
            self._pending_native_display_texts.remove(text)
            return
        if message_id == "current" and text in self._turn_displayed_texts:
            return  # Legacy hooks without an identity retain their redraw guard.
        self._unmatched_display_texts.append(text)
        self._turn_displayed_texts.append(text)
        if len(self._turn_displayed_texts) > 32:
            del self._turn_displayed_texts[:-32]
        await self._emit(
            {
                "type": "assistant",
                "message": {
                    "model": self._model or "interactive",
                    "content": [{"type": "text", "text": text}],
                },
                "metadata": {
                    "source": "claude_hook",
                    "hook_event_name": "MessageDisplay",
                    "claude_message_id": message_id,
                    "claude_session_id": payload.get("session_id"),
                    "transcript_path": payload.get("transcript_path"),
                },
            }
        )

    async def _emit_tool_use_from_hook(self, payload: dict[str, Any]) -> None:
        tool_name = self._coerce_str(payload.get("tool_name"))
        if not tool_name:
            return
        tool_input = payload.get("tool_input")
        tool_use_id = self._coerce_str(payload.get("tool_use_id")) or (f"hook-{uuid.uuid4().hex}")
        # CLI-mode questions bridge: the AskUserQuestion tool renders its menu in the TTY. Surface
        # it as a structured `ask_user_question` (its tool_input already carries the questions iOS
        # parses) so a remote client can answer; the tool_use is still emitted below for history.
        if tool_name == "AskUserQuestion" and isinstance(tool_input, dict):
            await self._surface_tty_ask_user_question(tool_input, payload=payload)
        # Parent attribution: a NON-Task tool firing while a Task subagent is in flight belongs
        # to that subagent (stack top). The Task tool's OWN tool_use is a main-agent action ->
        # parent stays None. Computed BEFORE the Task push below so a Task never self-references.
        parent_id = (
            self._coerce_str(payload.get("parent_tool_use_id"))
            or self._coerce_str(payload.get("parentToolUseId"))
            or None
        )
        if parent_id is None and tool_name != "Task" and self._active_subagent_stack:
            parent_id = self._active_subagent_stack[-1]
        # Plan surfacing: TodoWrite carries Claude's full task list each call.
        if tool_name == "TodoWrite" and isinstance(tool_input, dict):
            await self._surface_plan_from_todowrite(tool_input)
        # Agents surfacing: the Task tool spawns a subagent. Register it by
        # tool_use_id so the matching PostToolUse can mark it stopped.
        if tool_name == "Task" and isinstance(tool_input, dict):
            await self._surface_agent_started_from_task(tool_use_id, tool_input)
        tool_use_block: dict[str, Any] = {
            "type": "tool_use",
            "id": tool_use_id,
            "name": tool_name,
            "input": tool_input if isinstance(tool_input, dict) else {},
        }
        # Subagent attribution — added ONLY when a Task subagent is in flight, so a main-agent
        # tool frame stays byte-identical to before (absent keys decode as nil on iOS).
        if parent_id is not None:
            tool_use_block["parent_tool_use_id"] = parent_id
            tool_use_block["agent_id"] = parent_id
        await self._emit(
            {
                "type": "assistant",
                "message": {
                    "model": self._model or "interactive",
                    "content": [tool_use_block],
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
        question = next(
            (
                pending
                for pending in self._pending_tty_prompts.values()
                if pending.get("native_tool_use_id") == tool_use_id
                and pending.get("native_session_id") == payload.get("session_id")
            ),
            None,
        )
        if tool_use_id in self._question_result_ids:
            return
        # Parent attribution: compute BEFORE popping so the Task's OWN result carries parent=None
        # (a main-agent action) while a child result nests under the stack top.
        is_task_result = tool_use_id in self._hook_agents
        parent_id = (
            self._coerce_str(payload.get("parent_tool_use_id"))
            or self._coerce_str(payload.get("parentToolUseId"))
            or None
        )
        if parent_id is None and not is_task_result and self._active_subagent_stack:
            parent_id = self._active_subagent_stack[-1]
        # Agents surfacing: a finished Task tool means its subagent stopped — pop it off the
        # active-subagent stack so subsequent main-agent tools de-attribute. list.remove (not
        # pop()) tolerates an out-of-LIFO-order Task completion.
        if is_task_result:
            await self._finish_agent(tool_use_id, is_error=is_error)
            try:
                self._active_subagent_stack.remove(tool_use_id)
            except ValueError:
                pass
        result = payload.get("tool_response")
        if result is None:
            result = payload.get("error", "")
        tool_result_block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": self._stringify_hook_value(result),
            "is_error": is_error,
        }
        # Subagent attribution — added ONLY when this result belongs to a Task subagent, so a
        # main-agent result frame stays byte-identical to before.
        if parent_id is not None:
            tool_result_block["parent_tool_use_id"] = parent_id
            tool_result_block["agent_id"] = parent_id
        frame = {
            "type": "user",
            "message": {"content": [tool_result_block]},
            "metadata": {
                "source": "claude_hook",
                "hook_event_name": ("PostToolUseFailure" if is_error else "PostToolUse"),
                "claude_session_id": payload.get("session_id"),
                "transcript_path": payload.get("transcript_path"),
            },
        }
        if question is not None:
            response = payload.get("tool_response")
            question["native_result"] = {
                "is_error": is_error,
                "answers": response.get("answers") if isinstance(response, dict) else None,
                "frame": frame,
            }
            await self._emit_question_result_once(tool_use_id, frame)
            return
        await self._emit(frame)

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
        agent = {
            **agent,
            "status": "failed" if is_error else "done",
            # Pairs with the existing started_at so a client can show a duration, and so a
            # retained finished row is distinguishable from a live one by data, not by absence.
            "ended_at": datetime.now(UTC).isoformat(),
        }
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
        # Push the Task tool_use_id so subsequent NON-Task hooks (which fire while this Task
        # blocks its parent) attribute to this subagent. Popped in _emit_tool_result_from_hook.
        if tool_use_id and tool_use_id not in self._active_subagent_stack:
            self._active_subagent_stack.append(tool_use_id)

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
        # The hook's transcript_path is the ONLY place the subagent's JSONL location is
        # handed to us — register it now so the tracker can tail tokens for this agent id.
        transcript_path = self._coerce_str(payload.get("transcript_path"))
        if agent_id and transcript_path:
            self._agent_usage.register(agent_id, transcript_path)
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

    async def _finish_teammate_from_idle(self, payload: dict[str, Any]) -> None:
        """A TeammateIdle hook → evict the teammate from the running-agents registry.

        The registry keys teammates by ``pane_id`` (from ``terminal_pane_opened``), but the hook
        identifies the teammate by ``teammate_name`` (== the pane's ``--agent-name``). We resolve
        the pane by that name and emit a ``stopped`` ``agent_update`` on the SAME pane_id key so the
        broker's existing ``_track_agent_update`` pops the corpse. If the pane is already gone the
        stopped frame is keyed by name — harmless (dead-pane eviction already handled that case)."""
        teammate_name = self._coerce_str(payload.get("teammate_name"))
        if not teammate_name:
            return
        pane_id = next(
            (pid for pid, pane in self._panes.items() if pane.agent_name == teammate_name),
            None,
        )
        await self._emit_agent_update(
            {
                "id": pane_id or f"teammate:{teammate_name}",
                "kind": "teammate",
                "name": teammate_name,
                "status": "done",
            },
            action="stopped",
        )

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
        # In bypass mode AskUserQuestion still fires PermissionRequest for the
        # question itself. PreToolUse already surfaced that exact question. Keep
        # the advisory hook event, but do not invent a second authorization gate.
        if (
            self._skip_permissions
            and tool_name == "AskUserQuestion"
            and isinstance(tool_input, dict)
            and tool_input.get("questions")
            and any(
                pending.get("kind") == "question"
                and not pending.get("answer_uncertain")
                and pending.get("questions") == tool_input["questions"]
                for pending in self._pending_tty_prompts.values()
            )
        ):
            return
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
        self._cancel_submit_confirmations()
        self._pending_tty_prompts[request_id] = {
            "kind": "permission",
            "tool_name": tool_name,
            "questions": [question],
        }
        await self._emit_ask_user_question(request_id, [question])

    async def _surface_tty_ask_user_question(
        self, tool_input: dict[str, Any], *, payload: dict[str, Any] | None = None
    ) -> None:
        """Surface the AskUserQuestion tool's TTY menu as a structured `ask_user_question`.

        `tool_input.questions` is already in the shape iOS parses (header/question/options), so this
        is a pass-through; the on-screen menu mirrors those options, which `_answer_tty_prompt`
        matches by label at answer time.
        """
        questions = tool_input.get("questions")
        if not isinstance(questions, list) or not questions:
            return
        request_id = self._next_tty_request_id()
        self._cancel_submit_confirmations()
        native = self._question_native_identity(payload or {})
        self._pending_tty_prompts[request_id] = {
            "kind": "question",
            "questions": questions,
            **native,
        }
        await self._emit_ask_user_question(request_id, questions)

    def _question_native_identity(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Bind proof to this main native conversation and this exact tool call.

        The authenticated CLI hook supplies the transcript path. Its basename
        must identify the native session; inode/offset anchoring below prevents
        an old result or a replaced file from confirming a new answer.
        """
        tool_id = self._coerce_str(payload.get("tool_use_id"))
        native_id = self._coerce_str(payload.get("session_id"))
        if not tool_id or not native_id or native_id != self._claude_native_session_id:
            return {}
        try:
            native_id = str(uuid.UUID(native_id))
        except ValueError:
            return {}
        result: dict[str, Any] = {
            "native_tool_use_id": tool_id,
            "native_session_id": native_id,
        }
        path_text = self._coerce_str(payload.get("transcript_path"))
        if not path_text:
            return result
        path = Path(path_text)
        if not path.is_absolute() or path.name != f"{native_id}.jsonl":
            return result
        try:
            path = path.resolve(strict=True)
            stat = path.stat()
        except OSError:
            return result
        if path.name != f"{native_id}.jsonl" or not S_ISREG(stat.st_mode):
            return result
        result.update(
            transcript_path=str(path),
            transcript_offset=stat.st_size,
            transcript_device=stat.st_dev,
            transcript_inode=stat.st_ino,
        )
        return result

    def _read_native_question_result(self, pending: dict[str, Any]) -> dict[str, Any] | None:
        """Read only a bounded suffix added after this question was surfaced."""
        path_text = pending.get("transcript_path")
        if not path_text:
            return None
        try:
            # Non-blocking/no-follow open also closes the stat→open replacement
            # race: a FIFO cannot strand the proof worker before fstat rejects it.
            fd = os.open(path_text, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                stat = os.fstat(stream.fileno())
                if (
                    not S_ISREG(stat.st_mode)
                    or stat.st_dev != pending["transcript_device"]
                    or stat.st_ino != pending["transcript_inode"]
                    or stat.st_size < pending["transcript_offset"]
                ):
                    raise ControlRecoveryError("The native question transcript changed identity")
                stream.seek(pending["transcript_offset"])
                data = stream.read(self._question_transcript_max_bytes + 1)
        except OSError:
            return None
        if len(data) > self._question_transcript_max_bytes:
            raise ControlRecoveryError("Native question proof exceeded its bounded transcript read")
        found = None
        descendants: set[str] = set()
        for line in data.splitlines():
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue  # A concurrent writer may not have finished its last line.
            if not isinstance(record, dict) or record.get("isSidechain"):
                continue
            native_id = record.get("sessionId", record.get("session_id"))
            if native_id != pending["native_session_id"]:
                continue
            if found is not None:
                if record.get("parentUuid") not in descendants:
                    continue
                if record.get("type") == "system" and record.get("subtype") == "turn_duration":
                    found["turn_completed"] = True
                    return found
                if (
                    record.get("type") == "user"
                    and record.get("message")
                    == {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "[Request interrupted by user for tool use]"}
                        ],
                    }
                    and isinstance(record.get("uuid"), str)
                ):
                    descendants.add(record["uuid"])
                continue
            if record.get("type") != "user":
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    not isinstance(block, dict)
                    or block.get("type") != "tool_result"
                    or block.get("tool_use_id") != pending["native_tool_use_id"]
                ):
                    continue
                result = record.get("toolUseResult")
                found = {
                    "is_error": bool(block.get("is_error")),
                    "answers": result.get("answers") if isinstance(result, dict) else None,
                    "block": block,
                    "uuid": record.get("uuid"),
                }
                if isinstance(record.get("uuid"), str):
                    descendants.add(record["uuid"])
                break
        return found

    async def _native_question_result(self, pending: dict[str, Any]) -> dict[str, Any] | None:
        result = pending.get("native_result")
        if result is None:
            result = await asyncio.to_thread(self._read_native_question_result, pending)
        if result is None:
            return None
        pending["native_result"] = result
        tool_id = pending["native_tool_use_id"]
        frame = result.get("frame")
        if frame is None:
            frame = {
                "type": "user",
                "message": {"content": [result["block"]]},
                "metadata": {
                    "source": "claude_native_transcript",
                    "claude_session_id": pending["native_session_id"],
                    "claude_message_id": result.get("uuid"),
                    "transcript_path": pending.get("transcript_path"),
                },
            }
        await self._emit_question_result_once(tool_id, frame)
        return result

    async def _emit_question_result_once(self, tool_id: str, frame: dict[str, Any]) -> None:
        async with self._question_result_emit_lock:
            if tool_id in self._question_result_ids:
                return
            await self._emit(frame)
            # A failed persistence callback must leave emission retryable. The
            # native action is already consumed; this never replays TTY keys.
            self._question_result_ids.append(tool_id)

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
                "metadata": {
                    "source": "tmux_tty_bridge",
                    "control_kind": self._pending_tty_prompts[request_id]["kind"],
                    **(
                        {"answerable": False, "recovery_required": True}
                        if self._pending_tty_prompts[request_id].get("answer_uncertain")
                        else {}
                    ),
                },
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
        async with self._answer_lock:
            async with self._send_lock:
                await self._answer_tty_prompt_locked(request_id, answers, pane_id=pane_id)

    async def _answer_tty_prompt_locked(
        self, request_id: str | None, answers: object, *, pane_id: str | None = None
    ) -> None:
        """Drive the native control once; acknowledge questions only after native proof."""
        pending = self._pending_tty_prompts.get(request_id) if request_id else None
        if pending is None:
            raise ValueError("Unknown or already answered Claude question")
        if pending.get("answer_uncertain"):
            raise ControlRecoveryError(
                "The previous answer may have reached Claude. Inspect the native session "
                "before answering again."
            )

        kind = pending.get("kind")
        if kind == "permission":
            chosen = self._first_answer_text(answers)
            low = chosen.strip().casefold()
            if low not in {"allow", "allow & don't ask again", "deny"}:
                raise ValueError("The requested answer is not a declared Claude permission option")
            # Claimed for the rest of this branch (see the question-path comment below):
            # `_capture_menu_rows_wait` is a bounded poll with real `await`s, and a
            # concurrent turn-end signal must not clear this entry while we're waiting
            # on it — `_clear_pending_tty_prompts` skips any entry with `answer_in_flight`.
            pending["answer_in_flight"] = True
            try:
                rows = await self._capture_menu_rows_wait(pane_id=pane_id)
                if self._pending_tty_prompts.get(request_id) is not pending:
                    raise ValueError(
                        "The native Claude permission prompt ended while its menu was rendering"
                    )
                digit = self._permission_menu_digit(low, rows)
                if digit is None:
                    raise ValueError("The requested answer does not match the live Claude menu")
                pending["answer_uncertain"] = True
                await self._send_key("Escape" if low == "deny" else str(digit), pane_id=pane_id)
                await self._resolve_tty_answer(request_id, "deny" if low == "deny" else chosen)
            finally:
                pending.pop("answer_in_flight", None)
            return

        plans = self._question_answer_plans(pending.get("questions"), answers)
        if not pending.get("native_tool_use_id") or not pending.get("native_session_id"):
            raise ControlRecoveryError("This question has no verifiable native tool identity")
        # Claimed for the REST of this call, not just while driving keys: the render wait
        # below (`_wait_question_screen`) already spans real `await`s where a concurrent
        # turn-end signal (Stop hook, synthetic-turn watchdog) can land. `_clear_pending_
        # tty_prompts` skips any entry with `answer_in_flight` set, so setting it only
        # once we start pressing keys left this validation window unprotected — a pending
        # question could be silently dropped while we were still confirming its menu was
        # live, which is the actual bug: the client's answer would be refused for a
        # question that vanished attribution-free instead of failing loudly and visibly.
        pending["answer_in_flight"] = True
        try:
            result = await self._native_question_result(pending)
            if result is not None:
                await self._reject_native_question(request_id, result, pane_id=pane_id)
                raise ValueError("The native Claude question already completed before this answer")
            # Validate the first page before claiming an attempt. Later transitions
            # can already have consumed an answer, so any failure then is uncertain.
            try:
                await self._wait_question_screen(
                    lambda screen: self._question_page_matches(screen, plans[0]), pane_id=pane_id
                )
            except ValueError:
                result = await self._native_question_result(pending)
                if result is not None:
                    await self._reject_native_question(request_id, result, pane_id=pane_id)
                raise
            if self._pending_tty_prompts.get(request_id) is not pending:
                raise ValueError("The native Claude question ended while its menu was rendering")
            result = await self._native_question_result(pending)
            if result is not None:
                await self._reject_native_question(request_id, result, pane_id=pane_id)
                raise ValueError("The native Claude question already completed before this answer")
            pending["answer_uncertain"] = True
            try:
                for plan in plans:
                    await self._drive_question_page(plan, pane_id=pane_id)
                if len(plans) > 1 or any(plan["multi"] for plan in plans):
                    review = await self._wait_question_screen(
                        lambda screen: self._question_review_matches(screen, plans), pane_id=pane_id
                    )
                    digit = self._match_menu_digit("Submit answers", self._menu_rows(review))
                    await self._send_key(str(digit), pane_id=pane_id)
                result = await self._wait_native_question_result(pending)
                actual = result.get("answers")
                matched = (
                    not result["is_error"]
                    and isinstance(actual, dict)
                    and set(actual) == {plan["text"] for plan in plans}
                    and all(
                        self._question_answer_matches(actual[plan["text"]], plan) for plan in plans
                    )
                )
                if not matched:
                    await self._reject_native_question(request_id, result, pane_id=pane_id)
                    raise ValueError("Claude did not consume the requested question answers")
                decision = (
                    plans[0]["values"][0]
                    if len(plans) == 1 and not plans[0]["multi"]
                    else "answered"
                )
                await self._resolve_tty_answer(request_id, decision)
            except Exception as exc:
                if request_id in self._pending_tty_prompts:
                    result = await self._native_question_result(pending)
                    if result is not None and not pending.get("resolution_pending"):
                        await self._reject_native_question(request_id, result, pane_id=pane_id)
                        raise ValueError(
                            "Claude question completed outside the expected answer state"
                        ) from exc
                    await self._emit_ask_user_question(request_id, pending["questions"])
                    raise ControlRecoveryError(
                        "Claude answer consumption is uncertain; inspect the native question "
                        "before retrying"
                    ) from exc
                raise
        finally:
            pending.pop("answer_in_flight", None)

    async def _resolve_tty_answer(
        self, request_id: str | None, decision: str, *, accepted: bool = True
    ) -> None:
        pending = self._pending_tty_prompts.get(request_id)
        if pending is not None:
            pending["answer_uncertain"] = True
            pending["resolution_pending"] = True
        try:
            await self._emit_ask_user_resolved(request_id, decision, accepted=accepted)
        except Exception as exc:
            if pending is not None:
                await self._emit_ask_user_question(request_id, pending["questions"])
            raise ControlRecoveryError(
                "The native control completed but its acknowledgment could not be persisted"
            ) from exc
        self._pending_tty_prompts.pop(request_id, None)

    async def _reject_native_question(
        self, request_id: str | None, result: dict[str, Any], *, pane_id: str | None
    ) -> None:
        pending = self._pending_tty_prompts.get(request_id)
        await self._resolve_tty_answer(request_id, "native_answer_rejected", accepted=False)
        if not result["is_error"] or not self._turn_active:
            return
        # Cancellation skips PostToolUse/Stop in native Claude. Only an exact
        # failed tool result followed by its own descendant native turn_duration
        # record closes this turn. An idle-looking composer is not completion.
        deadline = time.monotonic() + max(self._question_result_wait_s, 0.0)
        while pending and pending.get("transcript_path") and not result.get("turn_completed"):
            refreshed = await asyncio.to_thread(self._read_native_question_result, pending)
            if refreshed is not None:
                result = refreshed
            if result.get("turn_completed") or time.monotonic() >= deadline:
                break
            await asyncio.sleep(self._menu_poll_step_s)
        if result.get("turn_completed"):
            await self._finish_hook_turn(
                content="", reason="native_question_rejected", is_error=True
            )

    @staticmethod
    def _question_answer_plans(questions: object, answers: object) -> list[dict[str, Any]]:
        """Validate every question/answer association before the first terminal write."""
        if (
            not isinstance(questions, list)
            or not questions
            or not all(isinstance(q, dict) for q in questions)
        ):
            raise ValueError("Claude question descriptors are invalid")
        if isinstance(answers, str) and len(questions) == 1:
            answers = [{"answer": answers}]
        if (
            not isinstance(answers, list)
            or len(answers) != len(questions)
            or not all(isinstance(a, dict) for a in answers)
        ):
            raise ValueError("Claude requires one explicit answer for every question")
        texts = [q.get("question") for q in questions]
        if any(not isinstance(t, str) or not t.strip() for t in texts) or len(set(texts)) != len(
            texts
        ):
            raise ValueError("Claude question identities are ambiguous")
        unused = list(answers)
        plans = []
        for index, question in enumerate(questions):
            text = question["question"]
            named = [
                a
                for a in unused
                if (question.get("id") and a.get("question_id") == question["id"])
                or a.get("question") == text
            ]
            if len(named) > 1:
                raise ValueError("Duplicate answers for a Claude question")
            answer = named[0] if named else answers[index]
            if answer not in unused or (
                not named and (answer.get("question_id") or answer.get("question"))
            ):
                raise ValueError("The answer does not identify the pending Claude question")
            unused.remove(answer)
            raw = answer.get("answer")
            values = raw if isinstance(raw, list) else [raw]
            if not values or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError("Claude question requires explicit non-empty answers")
            multi = bool(question.get("multiSelect"))
            if len(values) > 1 and not multi:
                raise ValueError("A single-select Claude question accepts only one option")
            options = question.get("options", [])
            if not isinstance(options, list):
                raise ValueError("Claude question options must be a list")
            labels = [
                o["label"]
                for o in options
                if isinstance(o, dict) and isinstance(o.get("label"), str)
            ]
            canonical = {label.strip().casefold(): label for label in labels}
            if len(canonical) != len(labels):
                raise ValueError("Claude option labels are ambiguous")
            free_text = answer.get("free_text")
            if free_text not in (None, "") and (
                not isinstance(free_text, str) or free_text not in values
            ):
                raise ValueError("Claude custom text disagrees with its answer")
            mapped = []
            custom = None
            for value in values:
                label = canonical.get(value.strip().casefold())
                if label is not None and free_text != value:
                    mapped.append(label)
                elif (not labels or free_text == value) and (
                    multi or not answer.get("option_indexes")
                ):
                    if custom is not None or any(ord(c) < 32 or ord(c) == 127 for c in value):
                        raise ValueError("Claude custom answer requires a single literal text row")
                    custom = value
                    mapped.append(value)
                else:
                    raise ValueError(
                        "The requested answer is not a declared Claude question option"
                    )
            if len(set(mapped)) != len(mapped):
                raise ValueError("Duplicate Claude option selections")
            if multi and custom in labels:
                raise ValueError(
                    "Claude checkbox custom text duplicates a declared label; "
                    "the native answer cannot distinguish these choices"
                )
            indexes = answer.get("option_indexes")
            if indexes is not None and not isinstance(indexes, list):
                raise ValueError("Claude option indexes must be a list")
            if indexes:
                if (
                    not isinstance(indexes, list)
                    or any(type(i) is not int or i < 0 or i >= len(labels) for i in indexes)
                    or len(set(indexes)) != len(indexes)
                    or {labels[i] for i in indexes}
                    != set(mapped) - ({custom} if custom is not None else set())
                ):
                    raise ValueError("Claude option indexes disagree with the selected labels")
            plans.append(
                {
                    "question": question,
                    "text": text,
                    "labels": labels,
                    "values": mapped,
                    "custom": custom,
                    "multi": multi,
                }
            )
        return plans

    @staticmethod
    def _question_answer_matches(actual: object, plan: dict[str, Any]) -> bool:
        values = plan["values"]
        if not plan["multi"]:
            return actual == values[0]
        if isinstance(actual, str):
            if actual == ", ".join(values):
                return True
            if any(", " in value for value in values):
                return False  # A lossy delimiter cannot prove a different ordering.
            actual = actual.split(", ")
        return (
            isinstance(actual, list)
            and len(actual) == len(values)
            and all(isinstance(v, str) for v in actual)
            and set(actual) == set(values)
        )

    @staticmethod
    def _menu_rows(screen: str) -> list[tuple[int, str]]:
        rows: list[tuple[int, str]] = []
        for line in screen.splitlines():
            match = _MENU_ROW_RE.match(line)
            if match:
                digit = int(match.group(1))
                if rows and digit <= rows[-1][0]:
                    rows.clear()
                rows.append((digit, match.group(2).strip()))
        return rows

    async def _capture_question_screen(self, *, pane_id: str | None = None) -> str:
        capture = await self._run_tmux("capture-pane", "-t", self._target_pane(pane_id), "-p")
        lines = _ANSI_RE.sub("", capture.stdout).splitlines()
        starts = [i for i, line in enumerate(lines) if "☐" in line or "☒" in line]
        return "\n".join(lines[starts[-1] :]) if starts else ""

    async def _wait_question_screen(
        self, predicate: Callable[[str], bool], *, pane_id: str | None = None
    ) -> str:
        deadline = time.monotonic() + max(self._menu_render_wait_s, 0.0)
        while True:
            screen = await self._capture_question_screen(pane_id=pane_id)
            if predicate(screen):
                return screen
            if time.monotonic() >= deadline:
                raise ValueError("The live Claude menu does not match the pending question state")
            await asyncio.sleep(self._menu_poll_step_s)

    @staticmethod
    def _question_page_matches(screen: str, plan: dict[str, Any]) -> bool:
        if not screen or "Review your answers" in screen:
            return False
        # Only the active widget is inspected; matching words in scrollback or
        # an earlier page cannot authorize another key.
        lines = screen.splitlines()
        first_row = next(
            (i for i, line in enumerate(lines) if _MENU_ROW_RE.match(line)), len(lines)
        )
        if " ".join(plan["text"].split()) != " ".join(" ".join(lines[1:first_row]).split()):
            return False
        rows = TmuxInteractiveTransport._menu_rows(screen)
        labels = []
        for _, label in rows:
            checkbox = re.match(r"^\[([ ✔xX])\]\s+(.+)$", label)
            if plan["multi"] and checkbox:
                label = checkbox.group(2)
            elif plan["multi"] and label != "Chat about this":
                return False
            labels.append(label)
        return all(label in labels for label in plan["labels"])

    @staticmethod
    def _question_review_matches(screen: str, plans: list[dict[str, Any]]) -> bool:
        if "Review your answers" not in screen or "Ready to submit your answers?" not in screen:
            return False
        if TmuxInteractiveTransport._menu_rows(screen) != [(1, "Submit answers"), (2, "Cancel")]:
            return False
        observed: dict[str, str] = {}
        question = None
        for line in screen.splitlines():
            value = line.strip()
            if value.startswith("● "):
                question = value[2:]
            elif value.startswith("→ ") and question is not None:
                if question in observed:
                    return False
                observed[question] = value[2:]
                question = None
        return set(observed) == {p["text"] for p in plans} and all(
            TmuxInteractiveTransport._question_answer_matches(observed[p["text"]], p) for p in plans
        )

    async def _drive_question_page(self, plan: dict[str, Any], *, pane_id: str | None) -> None:
        screen = await self._wait_question_screen(
            lambda s: self._question_page_matches(s, plan), pane_id=pane_id
        )
        rows = self._menu_rows(screen)
        if plan["multi"]:
            await self._drive_checkbox_page(plan, screen, pane_id=pane_id)
            return
        target = "Type something." if plan["custom"] is not None else plan["values"][0]
        digit = self._match_menu_digit(target, rows)
        if digit is None:
            raise ValueError("The requested answer does not match the live Claude menu")
        await self._send_key(str(digit), pane_id=pane_id)
        if plan["custom"] is not None:
            await self._enter_question_text(plan, digit, pane_id=pane_id)

    async def _enter_question_text(
        self, plan: dict[str, Any], digit: int, *, pane_id: str | None
    ) -> None:
        def editor(screen: str, text: str) -> bool:
            return (
                self._question_page_matches(screen, plan)
                and "ctrl+g to edit" in screen
                and any(
                    re.match(rf"^\s*❯\s+{digit}\.\s+(?:\[[ ✔xX]\]\s+)?{re.escape(text)}\s*$", line)
                    for line in screen.splitlines()
                )
            )

        await self._wait_question_screen(
            lambda s: editor(s, "Type something" + ("" if plan["multi"] else ".")), pane_id=pane_id
        )
        await self._paste_text(plan["custom"], enter=False, pane_id=pane_id)
        await self._wait_question_screen(lambda s: editor(s, plan["custom"]), pane_id=pane_id)
        # Single-select commits its populated editor with Enter. Checkbox text
        # is selected as it is typed; Enter there would toggle that answer OFF.
        if not plan["multi"]:
            await self._send_key("Enter", pane_id=pane_id)

    @staticmethod
    def _checkbox_values(screen: str) -> dict[str, tuple[int, bool]]:
        result = {}
        for digit, label in TmuxInteractiveTransport._menu_rows(screen):
            match = re.match(r"^\[([ ✔xX])\]\s+(.+)$", label)
            if match:
                result[match.group(2)] = (digit, match.group(1) != " ")
        return result

    async def _drive_checkbox_page(
        self, plan: dict[str, Any], screen: str, *, pane_id: str | None
    ) -> None:
        expected = set(plan["values"])
        for label in plan["labels"]:
            rows = self._checkbox_values(screen)
            digit, checked = rows[label]
            if checked != (label in expected):
                await self._send_key(str(digit), pane_id=pane_id)
                screen = await self._wait_question_screen(
                    lambda s: (
                        self._question_page_matches(s, plan)
                        and self._checkbox_values(s).get(label) == (digit, label in expected)
                    ),
                    pane_id=pane_id,
                )
        if plan["custom"] is not None:
            digit = self._checkbox_values(screen).get("Type something", (None, False))[0]
            if digit is None:
                raise ValueError("The native checkbox menu has no custom answer row")
            await self._focus_checkbox_other(plan, digit, screen, pane_id=pane_id)
            await self._enter_question_text(plan, digit, pane_id=pane_id)
        # Digit shortcuts toggle boxes without moving focus. Enter toggles the
        # focused box; it submits only after the unnumbered Submit gains focus.
        for _ in range(len(plan["labels"]) + 2):
            screen = await self._wait_question_screen(
                lambda s: self._question_page_matches(s, plan), pane_id=pane_id
            )
            checked = {
                label for label, (_, selected) in self._checkbox_values(screen).items() if selected
            }
            if checked != expected:
                raise ValueError("The native checkbox selections differ from the requested answers")
            if any(re.match(r"^\s*❯\s+Submit\s*$", line) for line in screen.splitlines()):
                await self._send_key("Enter", pane_id=pane_id)
                return
            focus = next(
                (line for line in screen.splitlines() if line.lstrip().startswith("❯")), None
            )
            if focus is None:
                raise ValueError("The native checkbox focus is unknown")
            await self._send_key("Down", pane_id=pane_id)
            await self._wait_question_screen(
                lambda s: self._question_page_matches(s, plan) and focus not in s.splitlines(),
                pane_id=pane_id,
            )
        raise ValueError("The native checkbox Submit control was not reached")

    async def _focus_checkbox_other(
        self, plan: dict[str, Any], digit: int, screen: str, *, pane_id: str | None
    ) -> None:
        # Checkbox digit shortcuts only toggle selection; they do not focus the
        # text editor. Navigate one observed focus change at a time instead.
        for _ in range(len(self._menu_rows(screen)) + 1):
            if any(re.match(rf"^\s*❯\s+{digit}\.\s+", line) for line in screen.splitlines()):
                return
            focus = next(
                (line for line in screen.splitlines() if line.lstrip().startswith("❯")), None
            )
            if focus is None:
                raise ValueError("The native checkbox focus is unknown")
            await self._send_key("Down", pane_id=pane_id)
            screen = await self._wait_question_screen(
                lambda s: self._question_page_matches(s, plan) and focus not in s.splitlines(),
                pane_id=pane_id,
            )
        raise ValueError("The native checkbox custom editor was not reached")

    async def _wait_native_question_result(self, pending: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + max(self._question_result_wait_s, 0.0)
        while True:
            result = await self._native_question_result(pending)
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                raise ControlRecoveryError(
                    "Claude has not confirmed consumption of this question answer"
                )
            await asyncio.sleep(self._menu_poll_step_s)

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
            result = await self._run_tmux("capture-pane", "-t", target, "-p")
        except Exception:  # pragma: no cover - capture is best-effort
            return []
        out: list[tuple[int, str]] = []
        for line in result.stdout.splitlines():
            match = _MENU_ROW_RE.match(line)
            if match:
                digit = int(match.group(1))
                # If two menus are still visible, the last menu owns input.
                # Never combine an old first row with a newer menu's later rows.
                if out and digit <= out[-1][0]:
                    out.clear()
                out.append((digit, match.group(2).strip()))
        return out

    @staticmethod
    def _match_menu_digit(chosen: str, rows: list[tuple[int, str]]) -> int | None:
        """Match a declared choice exactly; substrings can belong to unrelated menus."""
        target = chosen.strip().casefold()
        return next(
            (digit for digit, label in rows if label.strip().casefold() == target),
            None,
        )

    @staticmethod
    def _permission_menu_digit(chosen: str, rows: list[tuple[int, str]]) -> int | None:
        """Resolve only a recognizable permission menu, never an arbitrary first row."""
        choices: dict[str, int] = {}
        for digit, label in rows:
            low = label.strip().casefold()
            if re.match(r"^(yes|allow)(?:$|[, &])", low):
                if "don't ask" in low:
                    choices.setdefault("allow & don't ask again", digit)
                elif low in {"yes", "allow", "yes, allow this time", "allow once"}:
                    # A second affirmative row may grant a session-wide policy.
                    # Unknown scopes must not overwrite the one-time Allow row.
                    choices.setdefault("allow", digit)
            elif re.match(r"^(no|deny)(?:$|[, ])", low):
                choices["deny"] = digit
        if "allow" not in choices or "deny" not in choices:
            return None
        return choices.get(chosen)

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

    async def _emit_ask_user_resolved(
        self, request_id: str | None, decision: str, *, accepted: bool = True
    ) -> None:
        await self._emit(
            {
                "type": "ask_user_resolved",
                "event_type": "ask_user.resolved",
                "request_id": request_id or "",
                "decision": decision,
                "accepted": accepted,
                "metadata": {"source": "tmux_tty_bridge"},
            }
        )

    async def _clear_pending_tty_prompts(self, reason: str) -> None:
        """Resolve + drop any unanswered TTY prompts (turn ended / answered in-terminal) so a remote
        client dismisses its stale card instead of stranding it."""
        if not self._pending_tty_prompts:
            return
        stale = [
            rid
            for rid, pending in self._pending_tty_prompts.items()
            if not pending.get("answer_in_flight")
        ]
        for request_id in stale:
            self._pending_tty_prompts.pop(request_id, None)
            await self._emit_ask_user_resolved(request_id, reason, accepted=False)

    async def _finish_hook_turn(
        self,
        *,
        content: str,
        reason: str,
        is_error: bool = False,
    ) -> None:
        content = content.strip()
        # Twin guard: when the MessageDisplay hook already streamed this exact final message
        # mid-turn, re-emitting it here would append the same prose twice to the pending turn
        # (the reducer appends distinct segments; only an IDENTICAL trailing segment dedups,
        # and a tool frame may have landed in between). The result frame below still carries
        # the content either way.
        displayed = {self._normalize_prompt(t) for t in self._turn_displayed_texts}
        if content and self._normalize_prompt(content) not in displayed:
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
        # …and flush any pasted message still awaiting its per-message UserPromptSubmit: the CLI
        # batches queued steers into ONE submission (or absorbs them silently mid-turn), so the
        # later ones never fire their own hook and their steering_state strands at "pending"
        # (the greyed "queued" bubble that never flips) even though the reply addressed them.
        await self._flush_stale_prompt_correlations("turn_ended")
        self._turn_active = False
        if self._turn_done is not None:
            self._turn_done.set()

    async def _flush_stale_prompt_correlations(self, reason: str) -> None:
        """Turn boundary: emit the consumed signal for every delivered message that never got
        its own UserPromptSubmit correlation, so a batched/silently-absorbed steer cannot stay
        'pending' after the turn that absorbed it ended. Uses the SAME event shape as the real
        UserPromptSubmit path, so the broker's existing _activate_user_turn flips the bubble."""
        if not self._pending_prompt_correlations:
            return
        stale = list(self._pending_prompt_correlations)
        self._pending_prompt_correlations.clear()
        for msg_id, request_id, norm in stale:
            if not (msg_id or request_id):
                continue
            event: dict[str, Any] = {
                "type": "terminal_prompt_submitted",
                "event_type": "claude.prompt.submitted",
                "prompt": norm,
                "metadata": {"source": "turn_boundary_flush", "reason": reason},
            }
            if msg_id:
                event["msg_id"] = msg_id
            if request_id:
                event["request_id"] = request_id
            await self._emit(event)

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
        self._prepare_claude_config(env)
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
        if self._reasoning_effort:
            cmd.extend(["--effort", self._reasoning_effort])
        if self._resume_session_id:
            # Resume-aware restart: a fresh tmux launching ``claude --resume <id>`` replays the
            # prior conversation while re-writing the CURRENT broker port into the hook settings
            # (start() never resurrects an old tmux, so there is no stale-port baking).
            cmd.extend(["--resume", self._resume_session_id])
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
        if self._skip_permissions or (self._hook_events_enabled and self._sdk_port):
            cmd.extend(["--settings", str(self._hook_settings_path)])
        appended_system_prompt = self._composed_system_prompt()
        if appended_system_prompt:
            cmd.extend(["--append-system-prompt", appended_system_prompt])
        if self._mcp_config:
            cmd.extend(["--mcp-config", self._mcp_config])
        if self._agent_teams:
            cmd.extend(["--teammate-mode", "tmux"])
        return cmd

    def _composed_system_prompt(self) -> str:
        """Append capability help and session-supplied instructions, not workflow policy."""
        parts: list[str] = []
        if self._steering_instructions_enabled:
            parts.append(_STEERING_INPUT_INSTRUCTION)
        # Always advertise the present-file capability so any Forge agent can hand the user a file.
        parts.append(_PRESENT_FILE_INSTRUCTION)
        if self._system_prompt:
            parts.append(self._system_prompt)
        return "\n\n".join(parts)

    def _remote_control_name(self) -> str:
        """Label shown in the claude.ai/code + phone session list. Prefer the friendly
        Forge session name the broker exports; fall back to the session id."""
        raw = os.environ.get("SKULD__SESSION__NAME", "").strip() or self._forge_session_id
        return self._safe_name(raw)[:60]

    def _write_hook_settings(self) -> None:
        settings: dict[str, Any] = {}
        if self._skip_permissions:
            # Session settings belong in the CLI overlay: user settings can be
            # symlinked to a read-only Kubernetes credential projection.
            settings["skipDangerousModePermissionPrompt"] = True
        if self._hook_events_enabled and self._sdk_port:
            events = list(_CLAUDE_HOOK_EVENTS)
            if self._message_display_hook_enabled:
                events.extend(_OPTIONAL_HIGH_VOLUME_HOOK_EVENTS)
            hook = {
                "type": "http",
                "url": f"http://127.0.0.1:{self._sdk_port}/api/claude/hooks",
                "timeout": 5,
            }
            settings["hooks"] = {event: [{"matcher": "", "hooks": [hook]}] for event in events}
        if not settings:
            return
        self._hook_settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._hook_settings_path.write_text(
            json.dumps(settings, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _spawn_env(self) -> dict[str, str]:
        env = claude_spawn_env(
            gateway_url=self._model_gateway_url,
            gateway_token=self._model_gateway_token,
        )
        env["TERM"] = env.get("TERM") or "xterm-256color"
        if self._agent_teams:
            env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
        _, shim_env = ensure_codex_tool_shims(
            self.workspace_dir,
            mcp_servers=self._raw_mcp_servers,
        )
        env.update(shim_env)
        # The present-file shim POSTs here (broker app == sdk_port). Set for every engine so the
        # `present-file` command works in both claude and codex tmux sessions.
        if self._sdk_port:
            env["FORGE_PRESENT_FILE_URL"] = f"http://127.0.0.1:{self._sdk_port}/api/present-file"
        return env

    @staticmethod
    def _claude_config_path(env: dict[str, str]) -> Path:
        """The CLI's user config file: ``$CLAUDE_CONFIG_DIR/.claude.json`` when the
        directory is set (the session image sets it), else ``~/.claude.json``."""
        config_dir = env.get("CLAUDE_CONFIG_DIR", "").strip()
        if config_dir:
            return Path(config_dir) / ".claude.json"
        home = env.get("HOME", "").strip()
        return (Path(home) if home else Path.home()) / ".claude.json"

    def _prepare_claude_config(self, env: dict[str, str]) -> None:
        """Answer the CLI's first-run dialogs ahead of time.

        A session sandbox starts with no CLI state, and the interactive REPL
        then walks through onboarding (theme, then a login picker that does
        not count the OAuth token in the environment as a login), the
        workspace trust question, and the bypass-permissions notice. Nobody
        is at that keyboard; the broker is. So the answers a sandboxed session
        implies are written into the config the CLI reads before it starts:
        onboarding done, this workspace trusted, and the permission mode the
        session was configured with acknowledged.
        """
        config_path = self._claude_config_path(env)
        config: dict[str, Any] = {}
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
        before = json.dumps(config, sort_keys=True)
        config["hasCompletedOnboarding"] = True
        config.setdefault("theme", "dark")
        projects = config.setdefault("projects", {})
        project = projects.setdefault(self.workspace_dir, {})
        project["hasTrustDialogAccepted"] = True
        if json.dumps(config, sort_keys=True) != before:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            _replace_text_atomically(config_path, json.dumps(config, indent=2))
            logger.info("tmux: prepared Claude CLI config at %s (onboarding, trust)", config_path)

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
                "#{cursor_x}\t#{cursor_y}\t#{pane_dead}\t#{pane_title}"
            ),
            check=False,
        )
        if result.returncode != 0:
            # The tmux session is gone (panes can't be listed) — the CLI is dead.
            # Emit a one-shot transport_stopped so the broker can report the
            # ``stopped`` activity_state (clients otherwise see the last live
            # state frozen forever). Guard on the True→False edge so the polling
            # watcher fires it exactly once.
            was_alive = self._alive
            self._alive = False
            if was_alive and emit_events:
                with suppress(Exception):
                    await self._emit({"type": "transport_stopped", "reason": "tmux_session_gone"})
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
            pane_dead = (parts[9].strip() if len(parts) > 9 else "") == "1"
            # pane_title is last so an (unlikely) embedded tab is recovered intact.
            pane_title = "\t".join(parts[10:]).strip() if len(parts) > 10 else ""

            # DEAD pane: a corpse left listed by `remain-on-exit on` (kept for the primary REPL's
            # crash forensics). It never vanishes from `list-panes`, so the disappearance sweep
            # below would never fire — treat a dead pane as CLOSED here: evict tracking + emit close
            # event ONCE. A pane that is already dead the first time we see it was never registered,
            # so _evict_pane simply no-ops (no phantom open, no owed close).
            if pane_dead:
                await self._evict_pane(pane_id, window_name=window_name, emit_events=emit_events)
                continue

            seen.add(pane_id)
            existing = self._panes.get(pane_id)
            is_new = existing is None
            # `--agent-name` is immutable per pane, so resolve it once (a targeted tmux query, kept
            # off the multi-line `list-panes` format) and carry it forward on later polls.
            agent_name = (
                await self._resolve_pane_agent_name(pane_id) if is_new else existing.agent_name
            )
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
                pane_title=pane_title,
                agent_name=agent_name,
            )
            self._panes[pane_id] = pane
            if is_new:
                await self._start_pipe_for_pane(pane)
                if emit_events:
                    await self._emit_pane_opened(pane)

        for pane_id in set(self._panes) - seen:
            await self._evict_pane(pane_id, emit_events=emit_events)

    async def _resolve_pane_agent_name(self, pane_id: str) -> str:
        """Read the pane's `--agent-name` from its start command (agent-teams teammates only).

        Queried per-pane via ``display-message`` rather than folded into the ``list-panes`` format:
        the PRIMARY pane's start command carries a multi-line ``--append-system-prompt`` that would
        corrupt the line-delimited format, whereas a targeted read is single-value and safe. A pane
        with no ``--agent-name`` (the primary, or a manual shell split) returns ``""``."""
        result = await self._run_tmux(
            "display-message",
            "-p",
            "-t",
            pane_id,
            "#{pane_start_command}",
            check=False,
        )
        if result.returncode != 0:
            return ""
        return self._parse_agent_name(result.stdout)

    @staticmethod
    def _parse_agent_name(start_command: str) -> str:
        """Extract the value of ``--agent-name <name>`` from a pane start command, else ``""``."""
        match = re.search(r"--agent-name[=\s]+(\S+)", start_command or "")
        return match.group(1).strip("\"'") if match else ""

    async def _evict_pane(
        self, pane_id: str, *, window_name: str | None = None, emit_events: bool
    ) -> None:
        """Stop tracking a pane and emit a single ``terminal_pane_closed``. Shared by the two ways a
        pane leaves: it VANISHED from ``list-panes`` (normal close), or it went DEAD-but-listed
        under ``remain-on-exit``. A never-registered pane no-ops, so a dead pane we never opened
        stays unregistered and owes no close event."""
        pane = self._panes.pop(pane_id, None)
        if pane is None:
            return
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
                    "window_name": window_name if window_name is not None else pane.window_name,
                }
            )
            # Belt-and-suspenders teammate eviction: a teammate pane (identified by --agent-name)
            # that dies or vanishes emits an explicit `stopped` agent_update so a LIVE client drops
            # it immediately. terminal_pane_closed alone only makes the broker pop the registry row
            # SILENTLY, so a connected client would keep showing the corpse until its next reconnect
            # / REST poll. Pairs with the TeammateIdle finish signal: whichever fires first evicts.
            if pane.agent_name:
                await self._emit_agent_update(
                    {
                        "id": pane_id,
                        "kind": "teammate",
                        "name": pane.agent_name,
                        "status": "done",
                    },
                    action="stopped",
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
        # …and flush any pasted message still awaiting its per-message UserPromptSubmit: the CLI
        # batches queued steers into ONE submission (or absorbs them silently mid-turn), so the
        # later ones never fire their own hook and their steering_state strands at "pending"
        # (the greyed "queued" bubble that never flips) even though the reply addressed them.
        await self._flush_stale_prompt_correlations("turn_ended")
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
            # Fire-and-forget: the confirm loop must not delay send_message's return (the
            # send is non-blocking by contract) nor hold the send lock through its backoff.
            task = asyncio.create_task(
                self._confirm_submit(text, target),
                name=f"tmux-confirm-submit-{self._session_name}",
            )
            self._confirm_submit_tasks.add(task)
            task.add_done_callback(self._confirm_submit_tasks.discard)

    # Backoff schedule for re-pressing Enter while the composer still holds the pasted text.
    # The first check settles 0.25s after Enter (immediate captures still show the pre-submit
    # composer); the total budget stays well inside _deliver_timeout_s AND the 0.5s
    # send-is-non-blocking contract only pays the first hop (a cleared composer exits the loop).
    _SUBMIT_RETRY_DELAYS_S = (0.25, 0.6, 1.2)

    def _cancel_submit_confirmations(self) -> None:
        # The hook precedes the rendered menu. An Enter retry during that window
        # is buffered by the TTY and answers the upcoming question with its default.
        for task in list(self._confirm_submit_tasks):
            task.cancel()

    async def _confirm_submit(self, text: str, target: str) -> None:
        """Re-press Enter (bounded) while the pasted text still sits in the composer.

        A paste into a BUSY TUI (mid-turn steering) can race the Enter keystroke: the CLI
        is still processing the bracketed paste when Enter lands, so the message stays
        TYPED in the input box but never submits — the "first message goes through, later
        steers hang as sent-but-pending" wedge (the paste returned OK, the broker acked
        user_delivered, but UserPromptSubmit never fires and steering_state sticks at
        pending). Submission is confirmed by the composer CLEARING (our text tail leaves
        the bottom rows); while it hasn't, Enter is re-pressed with backoff. Guards:
        an extra Enter on an empty/cleared composer is a no-op, we only retry while OUR
        text is still visibly sitting in the input region, and we never press Enter when
        an interactive selection menu is open (it would answer the menu).
        """
        needle = self._normalize_prompt(text)
        if not needle:
            return
        needle = needle[-60:]
        for delay in self._SUBMIT_RETRY_DELAYS_S:
            await asyncio.sleep(delay)
            if self._pending_tty_prompts:
                return
            state = await self._composer_state(needle, target)
            if state != "holds":
                return
            logger.warning(
                "tmux deliver: composer still holds the message %.1fs after Enter — re-pressing",
                delay,
            )
            await self._send_key("Enter", pane_id=target)
        if await self._composer_state(needle, target) == "holds":
            logger.error(
                "tmux deliver: message may not have submitted — composer still shows it "
                "after %d Enter retries",
                len(self._SUBMIT_RETRY_DELAYS_S),
            )

    async def _composer_state(self, needle: str, target: str) -> str:
        """One bottom-of-pane observation: 'holds' when our text tail is still visible in
        the input region and no selection menu is open; 'menu' when a menu row is visible
        (never press Enter into it); 'clear' otherwise (submitted / can't tell)."""
        snapshot = await self._run_tmux(
            "capture-pane", "-p", "-S", "-12", "-t", target, check=False
        )
        if snapshot.returncode != 0:
            return "clear"
        rows = self._normalize_terminal_rows(snapshot.stdout)
        if any(_MENU_ROW_RE.match(row) for row in rows):
            return "menu"
        joined = self._normalize_prompt(" ".join(rows))
        return "holds" if needle in joined else "clear"

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
        # Never type the discovery probe (Escape / C-u / "/" / many Down keys) into a
        # still-booting Claude REPL — that corrupts the fresh session (garbled startup,
        # swallowed initial messages). Wait (bounded) for the input prompt
        # to render first, exactly like the seed-prompt delivery does.
        await self._wait_for_repl_ready()
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
                # Claude handles menu dismissal asynchronously. Without a render
                # tick, the menu consumes C-u and leaves '/' in the next prompt.
                await asyncio.sleep(self._menu_poll_step_s)
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
                "pane_title": pane.pane_title,
                "agent_name": pane.agent_name,
                "active": pane.active,
                "current_command": pane.current_command,
                "log_path": str(pane.log_path),
            }
        )

    @property
    def agent_usage(self) -> AgentUsageTracker:
        """Per-subagent token tracker. Duck-typed by the broker (``getattr(transport,
        "agent_usage", None)``) so non-tmux transports simply report no token totals."""
        return self._agent_usage

    @property
    def live_pane_ids(self) -> set[str]:
        """Pane ids currently tracked (registered, not evicted/dead). Lets the broker reap teammate
        rows whose pane no longer exists as a belt-and-suspenders over event-driven eviction."""
        return set(self._panes)

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
