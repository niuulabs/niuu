"""MuseMSPTransport — Meta Muse Code via the Muse Session Protocol (MSP) over stdio.

Spawns one long-lived ``muse serve`` host per Skuld session and drives it over MSP,
the open protocol Meta publishes for programmatic control of Muse Code
(github.com/meta-models/muse-code-sdk, MIT; the stable v1 schema is embedded in the
binary and exported with ``muse schema generate-json-schema``). It is JSON-RPC 2.0,
newline-delimited, one frame per line, over the host's stdin/stdout:

* ``initialize`` -> ``initialized`` (the SS1.4 handshake, sent exactly once),
* ``session/start`` or ``session/resume`` (durable sessions under ``~/.local/share/muse``),
* ``turn/start`` with ``ifBusy: queue | steer | replace`` — a mid-turn steer is NATIVE,
  nothing is interrupted to deliver it — plus ``turn/interrupt`` / ``turn/cancel``,
* a typed view stream: ``turn/started``, ``item/started|delta|updated|completed``,
  ``turn/completed`` (terminal ``completed | failed | cancelled`` with real token usage),
  ``session/tokenUsage``, ``approval/request`` and ``userInput/request`` (server ->
  client REQUESTS the client must answer), ``view/gap`` (dropped deliveries).

Every event is normalized to the Claude-style shapes the other Skuld transports emit
(assistant frames carrying ``tool_use`` blocks, ``user`` frames carrying ``tool_result``
blocks, ``content_block_delta`` text/thinking deltas, one ``result`` per turn) so the
broker, the transcript reducer, Ravn, usage reporting and every UI work unchanged.

Authentication: the host inherits the environment. ``META_API_KEY`` wins over a stored
key (``muse auth set --api-key-stdin``) which wins over a browser login. A host with no
credential still serves — the turn then ends ``failed`` with a ``modelError``
("not logged in: run /login to add an API key"), which this transport surfaces as an
error result rather than a wedge.

Model ids are whatever the Meta Model API serves (``muse-spark-1.3`` is the default;
``muse-spark-1.2`` and the ``-contributor`` variants are also real). Since Muse Code
1.0.x an unknown id is NOT rejected at ``session/start`` — the provider answers at the
first model call — so the id is checked against ``model/list`` at start and only warned
about; a retired/blank id is aliased to the default, mirroring the Grok transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from niuu.domain.reasoning import MODEL_EFFORTS, validate_effort
from niuu.domain.transcript_reducer import TOOL_ENDED_AT
from skuld.transports import (
    CLITransport,
    TransportCapabilities,
    _drain_stream,
    _filter_event,
    _stop_process,
)

logger = logging.getLogger("skuld.transport")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default model id. Overridden per session via Skuld session.model.
MUSE_DEFAULT_MODEL = "muse-spark-1.3"

# `initialize.clientInfo.name` MUST match ^[a-z0-9_]+$ (SS1.4.1) — "skuld-x" is rejected.
MSP_CLIENT_NAME = "skuld"
MSP_CLIENT_VERSION = "1.0"
MSP_SCHEMA_VERSION = 1

# Retired / shorthand ids clients may still send. A client is deployed separately from
# this server, so a stale id must upgrade rather than kill the session (the Grok
# "grok-build" lesson). Logged loudly so the skew is visible.
_LEGACY_MODEL_ALIASES: dict[str, str] = {
    "": MUSE_DEFAULT_MODEL,
    "muse": MUSE_DEFAULT_MODEL,
    "muse-spark": MUSE_DEFAULT_MODEL,
    "muse-code": MUSE_DEFAULT_MODEL,
    "meta-muse": MUSE_DEFAULT_MODEL,
}

# MSP `ReasoningEffort`. The Meta provider rejects "none", so it is dropped (host default).
_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "ultra"})
_REASONING_EFFORT_ALIASES: dict[str, str] = {
    "max": "ultra",
    "x-high": "xhigh",
    "extra-high": "xhigh",
}

# MSP `ApprovalMode` (closed enum, select-never-create) plus the spellings other engines use.
_APPROVAL_MODES = frozenset({"allowAll", "promptUnmatched", "onRequest", "denyUnmatched"})
_APPROVAL_MODE_ALIASES: dict[str, str] = {
    "allowall": "allowAll",
    "yolo": "allowAll",
    "never": "allowAll",  # `muse --approval-mode never`
    "bypasspermissions": "allowAll",
    "bypass": "allowAll",
    "onrequest": "onRequest",
    "on-request": "onRequest",
    "on_request": "onRequest",
    "default": "onRequest",
    "acceptedits": "onRequest",
    "promptunmatched": "promptUnmatched",
    "untrusted": "promptUnmatched",
    "denyunmatched": "denyUnmatched",
    "deny": "denyUnmatched",
    "plan": "denyUnmatched",
}

# Per-1M-token list prices (input, cached input, output) in USD — developer.meta.com
# /ai/models/muse-spark as of 2026-09-02. Used only to stamp ``costUSD`` on the result so
# the usage dashboards price Muse turns like Claude/Codex turns; unknown ids carry no cost.
_MODEL_PRICES_USD_PER_MTOK: dict[str, tuple[float, float, float]] = {
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.2": (1.25, 0.15, 4.25),
    "muse-spark-1.1": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
    "muse-spark-1.2-contributor": (0.10, 0.002, 0.20),
}
_TOKENS_PER_MILLION = 1_000_000

# Usage fallback when the host reports nothing for a turn (~4 chars/token) — only so
# message_count/usage still advance; real counts come from turn/completed.usage.
_CHARS_PER_TOKEN = 4

# MSP caps `freeText` / clarification `content` / `note` at 500 characters (tdd SS5.10.2).
_USER_INPUT_TEXT_LIMIT = 500

# view/gap splice-fill bounds (tdd SS4.8): pages of at most 1000 events, bounded so a
# runaway hole can never spin the reader.
_GAP_PAGE_LIMIT = 1000
_GAP_MAX_PAGES = 20

# Closed turns remembered so an ack that lands AFTER its turn's terminal still resolves.
_RECENT_RESULTS = 64

# Item statuses (open enum) that mean the tool call did not succeed.
_ERROR_ITEM_STATUSES = frozenset({"failed", "rejected", "timedOut", "cancelled"})

_STOP_REASON_BY_TERMINAL: dict[str, str] = {
    "completed": "end_turn",
    "cancelled": "cancelled",
    "failed": "error",
}


# ---------------------------------------------------------------------------
# Tool name mapping — Muse tool ids -> the cross-engine canonical spelling
# ---------------------------------------------------------------------------
#
# Keeping Bash/Edit/Read/Write identical across engines is what the hierarchical row
# classifier keys on. Anything unmapped falls back to the raw Muse name so a new tool
# still renders with a sensible title.
_MUSE_TOOL_MAP: dict[str, str] = {
    "shell": "Bash",
    "bash": "Bash",
    "run_shell": "Bash",
    "run_terminal_command": "Bash",
    "write_file": "Write",
    "write": "Write",
    "create_file": "Write",
    "edit_file": "Edit",
    "edit": "Edit",
    "apply_patch": "Edit",
    "search_replace": "Edit",
    "read_file": "Read",
    "read": "Read",
    "list_dir": "LS",
    "list_directory": "LS",
    "ls": "LS",
    "grep": "Grep",
    "search": "Grep",
    "glob": "Glob",
    "find_files": "Glob",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "fetch_url": "WebFetch",
    "request_user_input": "AskUserQuestion",
    "ask_user": "AskUserQuestion",
    "todo_write": "TodoWrite",
    "update_todo": "TodoWrite",
    "spawn_subagent": "Task",
    "subagent": "Task",
    "session_message_send": "SendMessage",
    "send_session_message": "SendMessage",
}


def _map_muse_tool(name: str) -> str:
    """Normalize a Muse tool id for UI parity (explicit map -> raw name)."""
    key = (name or "").strip()
    mapped = _MUSE_TOOL_MAP.get(key) or _MUSE_TOOL_MAP.get(key.lower())
    if mapped:
        return mapped
    return key or "tool"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """UTC timestamp in the shape the transcript reducer expects for tool timing."""
    return datetime.now(UTC).isoformat()


def _uuid7() -> str:
    """A UUIDv7 (RFC 9562): MSP command ids MUST be v7 (time-ordered) — SS3.1.1."""
    unix_ms = time.time_ns() // 1_000_000
    rand = uuid.uuid4().int & ((1 << 74) - 1)
    value = (
        (unix_ms << 80)
        | (0x7 << 76)
        | ((rand >> 62) << 64)
        | (0b10 << 62)
        | (rand & ((1 << 62) - 1))
    )
    return str(uuid.UUID(int=value))


def _resolve_model(model: str | None) -> str:
    """Map a retired/blank model id onto the default; pass real ids through."""
    raw = (model or "").strip()
    alias = _LEGACY_MODEL_ALIASES.get(raw.lower())
    if alias:
        if raw:
            logger.warning(
                "Muse model %r is a shorthand/retired id — using %r instead. "
                "The caller is on an old build; update it so the id it sends is real.",
                raw,
                alias,
            )
        return alias
    return raw


def _resolve_reasoning_effort(effort: str | None) -> str | None:
    """Validate a reasoning effort for the wire without silently changing a request."""
    raw = (effort or "").strip().lower()
    if not raw:
        return None
    raw = _REASONING_EFFORT_ALIASES.get(raw, raw)
    if raw in _REASONING_EFFORTS:
        return raw
    raise ValueError(f"Unsupported Muse reasoning effort: {effort}")


def _resolve_approval_mode(mode: str | None, *, default: str) -> str:
    raw = (mode or "").strip()
    if raw in _APPROVAL_MODES:
        return raw
    alias = _APPROVAL_MODE_ALIASES.get(raw.lower())
    if alias:
        return alias
    if raw:
        logger.warning("Muse approval mode %r is unknown — using %r", mode, default)
    return default


def _parse_args(raw: Any) -> dict | list | str:
    """Tool args arrive as the model's VERBATIM JSON string (tdd SS4.5.5); parse, tolerate junk."""
    if raw is None:
        return {}
    if isinstance(raw, dict | list):
        return raw
    text = str(raw).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {"raw": text}
    if isinstance(parsed, dict | list):
        return parsed
    return {"raw": text}


def _subject_summary(subject: Any) -> dict:
    """The approval's `subject` (fileAccess / shell / network ...) flattened for a prompt."""
    if not isinstance(subject, dict):
        return {}
    keep = {}
    for key in ("kind", "toolName", "path", "access", "command", "url", "host", "port"):
        if subject.get(key) not in (None, ""):
            keep[key] = subject[key]
    return keep


def _claude_questions(questions: Any) -> list[dict]:
    """MSP `UserInputQuestion[]` -> the AskUserQuestion shape the clients render."""
    out: list[dict] = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        selection = q.get("selection") if isinstance(q.get("selection"), dict) else {}
        options = []
        for opt in q.get("options") or []:
            if not isinstance(opt, dict):
                continue
            entry = {
                "label": str(opt.get("label") or ""),
                "description": str(opt.get("description") or ""),
            }
            preview = opt.get("preview")
            if isinstance(preview, dict) and preview.get("content"):
                entry["preview"] = str(preview.get("content"))
            options.append(entry)
        out.append(
            {
                "id": str(q.get("id") or ""),
                "question": str(q.get("question") or ""),
                "header": str(q.get("header") or ""),
                "options": options,
                "multiSelect": selection.get("mode") == "multiple",
            }
        )
    return out


def _clip(text: str, limit: int = _USER_INPUT_TEXT_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _msp_answers(questions: list[dict], answers: Any) -> list[dict]:
    """Client answers (aligned to questions, each `{"answer": str | list[str]}` or bare) ->
    MSP `UserInputAnswer[]`: exactly one of selectedLabel / selectedLabels / freeText."""
    answer_list = answers if isinstance(answers, list) else []
    out: list[dict] = []
    for i, q in enumerate(questions):
        entry = answer_list[i] if i < len(answer_list) else None
        chosen: Any = entry
        if isinstance(entry, dict):
            for key in ("answer", "selected", "labels", "label", "value", "text"):
                if entry.get(key) not in (None, ""):
                    chosen = entry[key]
                    break
            else:
                chosen = None
        if isinstance(chosen, list | tuple):
            values = [str(c) for c in chosen if str(c)]
        elif chosen in (None, ""):
            values = []
        else:
            values = [str(chosen)]
        labels = {
            str(o.get("label") or "") for o in (q.get("options") or []) if isinstance(o, dict)
        }
        selection = q.get("selection") if isinstance(q.get("selection"), dict) else {}
        qid = str(q.get("id") or "")
        if selection.get("mode") == "multiple":
            picked = [v for v in values if v in labels]
            if picked:
                out.append({"questionId": qid, "selectedLabels": picked})
            else:
                out.append(
                    {"questionId": qid, "freeText": _clip(", ".join(values) or "(no answer)")}
                )
            continue
        matched = next((v for v in values if v in labels), None)
        if matched is not None:
            out.append({"questionId": qid, "selectedLabel": matched})
        else:
            out.append({"questionId": qid, "freeText": _clip(", ".join(values) or "(no answer)")})
    return out


def _pick_choice(choices: Any, behavior: str) -> dict | None:
    """Choose one of the SERVER-MINTED approval choices (D-006) for a broker behaviour."""
    rows = [c for c in (choices or []) if isinstance(c, dict) and c.get("choiceId")]
    if not rows:
        return None
    if behavior == "deny":
        wanted = ("denied", "deniedPolicyAmendment", "abort")
    elif behavior == "allowForever":
        wanted = ("approvedForSession", "approvedPolicyAmendment", "approved")
    else:
        wanted = ("approved", "approvedForSession", "approvedPolicyAmendment")
    for decision in wanted:
        for row in rows:
            if row.get("decision") == decision:
                return row
    # A decision vocabulary this build predates: fall back on the label/scope hints.
    for row in rows:
        decision = str(row.get("decision") or "").lower()
        if behavior == "deny" and ("den" in decision or "abort" in decision):
            return row
        if behavior != "deny" and "approv" in decision:
            return row
    return rows[0] if behavior != "deny" else None


class MuseProtocolError(RuntimeError):
    """A JSON-RPC error answered by the host (code + typed `data.kind`)."""

    def __init__(self, error: dict | None) -> None:
        error = error if isinstance(error, dict) else {}
        data = error.get("data") if isinstance(error.get("data"), dict) else {}
        self.code = error.get("code")
        self.kind = str(data.get("kind") or "")
        self.reason = str(data.get("reason") or "")
        self.data = data
        self.message = str(error.get("message") or "muse protocol error")
        super().__init__(f"{self.message} (code={self.code} kind={self.kind or '-'})")


@dataclass
class _TurnState:
    """Everything one MSP turn accumulates until its `turn/completed`."""

    turn_id: str
    future: asyncio.Future
    started: bool = False
    watchdog: asyncio.Task | None = None
    correlation: tuple[str | None, str | None] | None = None
    consumed_emitted: bool = False
    text_parts: list[str] = field(default_factory=list)
    in_chars: int = 0
    out_chars: int = 0
    reason_chars: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    usage_model: str | None = None
    started_mono: float = field(default_factory=time.monotonic)


class MuseMSPTransport(CLITransport):
    """Persistent MSP client over ``muse serve`` (Meta Muse Code, Scaldy pipeline).

    - Spawns one long-lived ``muse serve`` host (sandbox posture is fixed per host;
      approval mode is chosen per session on the wire).
    - Performs the ``initialize``/``initialized`` handshake and ``session/start`` (or
      ``session/resume`` for an imported native session id) on start().
    - send_message() submits a ``turn/start`` and waits for that turn's terminal;
      send_control("redirect") steers the running turn natively (``ifBusy: steer``).
    - The always-on reader folds the view stream into Claude-style events and emits one
      ``result`` per turn — including on timeout, host death and teardown, so a turn can
      never be left open (the invariant the Grok transport learnt the hard way).
    """

    def __init__(
        self,
        workspace_dir: str,
        model: str = MUSE_DEFAULT_MODEL,
        session_id: str | None = None,
        resume_session_id: str | None = None,
        muse_bin: str | None = None,
        skip_permissions: bool = True,
        agent_teams: bool = False,
        system_prompt: str = "",
        initial_prompt: str = "",
        reasoning_effort: str = "",
        acp_prompt_timeout_s: float = 300.0,
        msp_handshake_timeout_s: float = 60.0,
        msp_interrupt_grace_s: float = 15.0,
        msp_shutdown_grace_s: float = 5.0,
        disable_sandbox: bool = True,
        sandbox_network: str = "",
        trust_workspace: bool = True,
        provider_id: str | None = None,
        approval_mode: str = "",
        **_: Any,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = _resolve_model(model)
        self._skuld_session_id = session_id
        self._resume_session_id = (resume_session_id or "").strip()
        self._muse_bin_override = muse_bin
        self._skip_permissions = skip_permissions
        self._agent_teams = agent_teams  # accepted for parity; Muse fans out on its own
        if system_prompt:
            raise ValueError(
                "Muse MSP cannot apply system_prompt (including project briefings); "
                "use a runtime with system-prompt support, or configure Muse workspace AGENTS.md"
            )
        self._initial_prompt = initial_prompt
        self._reasoning_effort = _resolve_reasoning_effort(reasoning_effort)
        self._prompt_timeout = acp_prompt_timeout_s
        self._handshake_timeout = msp_handshake_timeout_s
        self._interrupt_grace = msp_interrupt_grace_s
        self._shutdown_grace = msp_shutdown_grace_s
        self._disable_sandbox = disable_sandbox
        self._sandbox_network = (sandbox_network or "").strip()
        self._trust_workspace = trust_workspace
        self._provider_id = (provider_id or "").strip() or None
        self._approval_mode = _resolve_approval_mode(
            approval_mode, default="allowAll" if skip_permissions else "onRequest"
        )

        self._process: asyncio.subprocess.Process | None = None
        self._starting = False
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._initial_dispatch_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()  # serialize send_message turns
        self._write_lock = asyncio.Lock()  # one frame at a time on stdin

        # JSON-RPC bookkeeping
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._early_results: dict[int, dict] = {}

        # Session state
        self._muse_session_id: str | None = None
        self._session_loaded = False
        self._server_info: dict[str, Any] = {}
        self._session_durability = "durable"
        self._last_result: dict | None = None

        # Turn / item fold
        self._turns: dict[str, _TurnState] = {}
        self._active_turn_id: str | None = None
        self._items: dict[str, dict[str, Any]] = {}
        self._cumulative_usage: dict[str, Any] = {}
        self._context_usage: dict[str, Any] = {}
        self._current_plan: list[dict] = []
        self._recent_results: OrderedDict[str, dict] = OrderedDict()
        # Work the reader must NOT await inline (anything that itself waits for a host
        # response would deadlock the only coroutine that reads responses).
        self._bg_tasks: set[asyncio.Task] = set()

        # Human gates
        self._pending_user_input: dict[str, Any] | None = None
        self._pending_approvals: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Emit helpers
    # ------------------------------------------------------------------

    async def _emit(self, data: dict) -> None:
        """Emit an event, tolerating both sync and async registered callbacks."""
        if not self._event_callback:
            logger.debug("_emit: no callback registered, dropping type=%s", data.get("type"))
            return
        result = self._event_callback(data)
        if asyncio.iscoroutine(result):
            await result

    async def _emit_filtered(self, data: dict | None) -> None:
        if not data:
            return
        filtered = _filter_event(data)
        if filtered:
            await self._emit(filtered)

    def _assistant_event(self, blocks: list[dict]) -> dict:
        """An assistant frame in the shape the live broker path reads (message.content)."""
        return {
            "type": "assistant",
            "message": {"model": self._model, "content": blocks},
            "content": blocks,
        }

    @staticmethod
    def _user_event(blocks: list[dict]) -> dict:
        """A user frame — the role a tool_result must ride in."""
        return {"type": "user", "message": {"content": blocks}, "content": blocks}

    async def _emit_text(self, state: _TurnState | None, text: str) -> None:
        if not text:
            return
        if state is not None:
            state.text_parts.append(text)
            state.out_chars += len(text)
        await self._emit_filtered(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
        )

    async def _emit_thinking(self, state: _TurnState | None, text: str) -> None:
        if not text:
            return
        if state is not None:
            state.reason_chars += len(text)
        await self._emit_filtered(
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": text}}
        )

    async def _emit_user_consumed(self, correlation: tuple[str | None, str | None] | None) -> None:
        """Tell the broker a steered user message was consumed (its turn/start was accepted),
        so it flips that message pending -> active. No-ops without a msg_id."""
        if not correlation or not correlation[0]:
            return
        msg_id, request_id = correlation
        event: dict = {
            "type": "user_consumed",
            "event_type": "muse.turn.accepted",
            "msg_id": msg_id,
        }
        if request_id:
            event["request_id"] = request_id
        await self._emit(event)

    def _spawn(self, coro: Any, name: str) -> None:
        """Run host-facing work off the reader loop (a reader that awaits a response it is
        the only one able to read would deadlock)."""
        task = asyncio.create_task(coro, name=name)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _flip_consumed(self, state: _TurnState) -> None:
        """Flip the turn's steering bubble pending -> active exactly once."""
        if state.consumed_emitted or state.correlation is None:
            return
        state.consumed_emitted = True
        await self._emit_user_consumed(state.correlation)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _resolve_muse_bin(self) -> str:
        return self._muse_bin_override or shutil.which("muse") or "muse"

    def _serve_command(self, muse_bin: str) -> list[str]:
        """``muse serve`` with the host-lifetime sandbox posture (flags go AFTER the verb)."""
        cmd = [muse_bin, "serve"]
        if self._disable_sandbox:
            # Muse's sandbox is bubblewrap on Linux and fails CLOSED in a container without
            # the capabilities to build it; Skuld runs the agent in its own workspace pod, so
            # the sandbox is off by default exactly like Claude's skip-permissions mode.
            cmd.append("--disable-sandbox")
        elif self._sandbox_network:
            cmd.extend(["--sandbox-network", self._sandbox_network])
        if self._trust_workspace:
            # Load the workspace's AGENTS.md / CLAUDE.md rules and skills (an untrusted
            # workspace gets none of them — the agent would be flying blind in the repo).
            cmd.append("--trust-workspace")
        return cmd

    async def start(self) -> None:
        logger.info(
            "MuseMSPTransport configured for %s (model: %s, approval: %s, effort: %s, "
            "resume: %s, initial_prompt=%d chars)",
            self.workspace_dir,
            self._model,
            self._approval_mode,
            self._reasoning_effort or "host-default",
            self._resume_session_id or "-",
            len(self._initial_prompt or ""),
        )
        # START-ONCE: the flag is set BEFORE any await so a concurrent start() cannot slip
        # past the guard while the handshake is in flight (two readers on one stdout is the
        # `readuntil() called while another coroutine is already waiting` failure).
        if self._starting or self._process is not None:
            logger.debug("MuseMSPTransport.start() ignored — already starting/started")
            return
        self._starting = True

        try:
            await self._start_host()
        except BaseException:
            await self._teardown_process()
            raise

    async def _start_host(self) -> None:
        muse_bin = self._resolve_muse_bin()
        if not self._credentials_present():
            logger.warning(
                "No Muse credential found (META_API_KEY unset and no ~/.config/muse/auth.json) — "
                "the host will serve, but every turn will fail with 'not logged in' until one is "
                "provided"
            )
        cmd = self._serve_command(muse_bin)
        logger.info("Spawning Muse MSP host: %s", " ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workspace_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ},  # inherits META_API_KEY / stored auth
        )
        self._process = process
        if process.stdout is None or process.stdin is None:
            raise RuntimeError("Muse MSP stdio pipes not available")

        self._stderr_task = asyncio.create_task(_drain_stream(process.stderr, "muse-stderr"))
        self._reader_task = asyncio.create_task(self._reader_loop())

        await self._msp_initialize()
        await self._check_model_catalog()
        await self._open_session()

        logger.info(
            "Muse MSP session ready: %s (sessionId=%s, host=%s %s)",
            self._model,
            self._muse_session_id,
            self._server_info.get("name"),
            self._server_info.get("version"),
        )

        # Forge auto-start seeds the task via initial_prompt and relies on the transport to
        # dispatch it (MSP has no separate "initial prompt" slot). Fire-and-forget so start()
        # returns promptly; the turn streams via the reader.
        if self._initial_prompt and not self._resume_session_id:
            logger.info(
                "Muse MSP: dispatching seeded initial prompt (%d chars)", len(self._initial_prompt)
            )
            self._initial_dispatch_task = asyncio.create_task(
                self.send_message(self._initial_prompt)
            )

    @staticmethod
    def _credentials_present() -> bool:
        if os.environ.get("META_API_KEY"):
            return True
        config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
        return os.path.exists(os.path.join(config_home, "muse", "auth.json"))

    async def _ensure_started(self) -> None:
        if self._process is None or self._process.returncode is not None:
            self._process = None
            self._starting = False
            await self.start()

    async def _teardown_process(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        process = self._process
        if process is not None:
            try:
                if process.stdin is not None and not process.stdin.is_closing():
                    process.stdin.close()  # EOF first so the host can drain (SS2.1.2)
                await asyncio.wait_for(process.wait(), timeout=self._shutdown_grace)
            except Exception:
                await _stop_process(process)
        self._process = None
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)
        self._stderr_task = None
        self._starting = False
        self._reader_task = None
        self._session_loaded = False
        self._pending.clear()
        self._early_results.clear()

    async def stop(self) -> None:
        # Close every open turn BEFORE the host goes: the broker only closes a turn on a
        # `result`, so a stop mid-turn without one strands the session forever.
        for turn_id in list(self._turns):
            state = self._turns.get(turn_id)
            if state is not None and not state.future.done():
                await self._finalize_stranded_turn(state, "cancelled")
        if self._initial_dispatch_task and not self._initial_dispatch_task.done():
            self._initial_dispatch_task.cancel()
        for task in list(self._bg_tasks):
            task.cancel()
        async with self._send_lock:
            await self._teardown_process()
            self._active_turn_id = None
            self._items.clear()
            self._pending_user_input = None
            self._pending_approvals.clear()

    # ------------------------------------------------------------------
    # JSON-RPC over stdio
    # ------------------------------------------------------------------

    async def _write_frame(self, payload: dict) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("Muse MSP host not started")
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
        async with self._write_lock:
            process.stdin.write(line)
            await process.stdin.drain()

    async def _request(self, method: str, params: dict[str, Any] | None, *, timeout: float) -> dict:
        """Send a JSON-RPC request and await its result (resolved by the reader)."""
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params:
            payload["params"] = params
        try:
            await self._write_frame(payload)
        except Exception:
            self._pending.pop(req_id, None)
            raise
        # The reader may have answered (and buffered) before the future was registered.
        early = self._early_results.pop(req_id, None)
        if early is not None:
            self._pending.pop(req_id, None)
            if "error" in early:
                raise MuseProtocolError(early["error"])
            return early.get("result") or {}
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    async def _notify(self, method: str, params: dict | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            payload["params"] = params
        await self._write_frame(payload)

    async def _respond(
        self, req_id: Any, *, result: dict | None = None, error: dict | None = None
    ) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result if result is not None else {}
        try:
            await self._write_frame(payload)
        except Exception as exc:  # pragma: no cover - pipe teardown races
            logger.warning("Muse MSP failed to answer request %s: %r", req_id, exc)

    async def _command(
        self, method: str, params: dict[str, Any], *, timeout: float | None = None
    ) -> dict:
        """A standard SS3 command: session-scoped, carries a fresh UUIDv7 commandId."""
        body = {"sessionId": self._muse_session_id, "commandId": _uuid7(), **params}
        return await self._request(method, body, timeout=timeout or self._handshake_timeout)

    async def _reader_loop(self) -> None:
        """Read frames until EOF: notifications fold, responses resolve, requests are answered."""
        process = self._process
        assert process is not None and process.stdout is not None
        reader = process.stdout
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                raw = line.decode(errors="replace").strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("Muse MSP non-JSON line (ignored): %s", raw[:160])
                    continue
                if not isinstance(data, dict):
                    continue
                try:
                    await self._dispatch_frame(data)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Muse MSP frame handling failed: %r (%s)", exc, raw[:200])
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Muse MSP reader loop error: %r", exc)
        finally:
            logger.info("Muse MSP reader loop exited")
            await self._on_host_eof()

    async def _dispatch_frame(self, data: dict) -> None:
        method = data.get("method")
        if method and "id" in data:
            # A server -> client REQUEST (approval/request, userInput/request). Must be tested
            # BEFORE the response branch: it carries an id too, and an unanswered request
            # blocks the agent until the prompt timeout.
            await self._on_server_request(data["id"], str(method), data.get("params") or {})
            return
        if method:
            await self._on_notification(str(method), data.get("params") or {})
            return
        if "id" in data:
            req_id = data["id"]
            fut = self._pending.pop(req_id, None)
            if fut is not None and not fut.done():
                if "error" in data:
                    fut.set_exception(MuseProtocolError(data["error"]))
                else:
                    fut.set_result(data.get("result") or {})
            elif fut is None:
                self._early_results[req_id] = data
            return
        logger.debug("Muse MSP unhandled frame: %s", str(data)[:200])

    async def _on_host_eof(self) -> None:
        """The host closed stdout: fail the in-flight requests and close open turns."""
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(RuntimeError("Muse MSP host closed the connection"))
        self._pending.clear()
        self._session_loaded = False
        for turn_id in list(self._turns):
            state = self._turns.get(turn_id)
            if state is not None and not state.future.done():
                await self._finalize_stranded_turn(state, "error", detail="the Muse host exited")

    # ------------------------------------------------------------------
    # Handshake + session
    # ------------------------------------------------------------------

    async def _msp_initialize(self) -> None:
        result = await self._request(
            "initialize",
            {"clientInfo": {"name": MSP_CLIENT_NAME, "version": MSP_CLIENT_VERSION}},
            timeout=self._handshake_timeout,
        )
        self._server_info = result.get("serverInfo") or {}
        schema = result.get("schema") or {}
        if schema.get("version") not in (None, MSP_SCHEMA_VERSION):
            logger.warning(
                "Muse MSP envelope schema version %s != %s — this transport was written "
                "against v1; proceeding on the additive-evolution promise",
                schema.get("version"),
                MSP_SCHEMA_VERSION,
            )
        self._session_durability = str(result.get("sessionDurability") or "durable")
        logger.debug(
            "Muse MSP initialize: server=%s schema=%s durability=%s caps=%s",
            self._server_info,
            schema,
            self._session_durability,
            result.get("grantedCapabilities"),
        )
        await self._notify("initialized")

    async def _check_model_catalog(self) -> None:
        """Warn (never fail) when the requested id is absent from the host's catalog."""
        try:
            result = await self._request("model/list", {}, timeout=self._handshake_timeout)
        except Exception as exc:
            logger.debug("Muse MSP model/list unavailable: %r", exc)
            return
        ids = [m.get("modelId") for m in result.get("models") or [] if isinstance(m, dict)]
        if ids and self._model not in ids:
            logger.warning(
                "Muse model %r is not in the host catalog %s (source=%s) — the provider "
                "decides at the first model call",
                self._model,
                ids,
                result.get("source"),
            )

    async def _open_session(self) -> None:
        if self._resume_session_id:
            await self._resume_session(self._resume_session_id)
            return
        params: dict[str, Any] = {
            "commandId": _uuid7(),
            "workspaceRoot": os.path.abspath(self.workspace_dir),
            "modelId": self._model,
            "approvalMode": self._approval_mode,
        }
        if self._provider_id:
            params["providerId"] = self._provider_id
        result = await self._request("session/start", params, timeout=self._handshake_timeout)
        session = result.get("session") or {}
        self._muse_session_id = str(session.get("sessionId") or "") or None
        self._session_loaded = True
        logger.info("Muse MSP session started: %s (%s)", self._muse_session_id, session.get("path"))

    async def _resume_session(self, session_id: str) -> None:
        result = await self._request(
            "session/resume",
            {"commandId": _uuid7(), "sessionId": session_id, "excludeItems": True},
            timeout=self._handshake_timeout,
        )
        session = result.get("session") or {}
        self._muse_session_id = str(session.get("sessionId") or session_id)
        self._session_loaded = True
        if session.get("modelId"):
            self._model = str(session["modelId"])
        active = session.get("activeTurnId")
        if session.get("status") == "running" and active:
            # A turn was in flight when the previous host went away — adopt it so its
            # terminal closes a real turn and the watchdog bounds it.
            state = self._ensure_turn(str(active))
            state.started = True
            self._active_turn_id = state.turn_id
            self._start_watchdog(state)
        logger.info(
            "Muse MSP session resumed: %s (status=%s, pending=%d)",
            self._muse_session_id,
            session.get("status"),
            len(result.get("pendingRequests") or []),
        )

    async def _ensure_session_loaded(self) -> None:
        if self._session_loaded or not self._muse_session_id:
            return
        # The host unloaded the session (idle policy / session/closed): reload it in place.
        await self._resume_session(self._muse_session_id)

    # ------------------------------------------------------------------
    # Turns
    # ------------------------------------------------------------------

    def _ensure_turn(self, turn_id: str) -> _TurnState:
        state = self._turns.get(turn_id)
        if state is None:
            state = _TurnState(turn_id=turn_id, future=asyncio.get_running_loop().create_future())
            self._turns[turn_id] = state
        return state

    def _start_watchdog(self, state: _TurnState) -> None:
        if state.watchdog is not None and not state.watchdog.done():
            return
        state.watchdog = asyncio.create_task(
            self._watch_turn(state), name=f"muse-turn-{state.turn_id[:8]}"
        )

    async def _watch_turn(self, state: _TurnState) -> None:
        """Bound a turn: on timeout interrupt it, then close it if the host stays silent.

        A TIMEOUT MUST STILL END THE TURN. The broker only closes a turn when a `result`
        arrives; anything that abandons the wait without one leaves the session permanently
        "working" with every later message queued behind it.
        """
        try:
            await asyncio.wait_for(asyncio.shield(state.future), timeout=self._prompt_timeout)
            return
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        logger.warning(
            "Muse MSP turn %s did not complete within %.0fs — interrupting it so the session "
            "does not wedge",
            state.turn_id,
            self._prompt_timeout,
        )
        try:
            await asyncio.wait_for(
                self._interrupt_turn(state.turn_id, reason="timeout"), timeout=self._interrupt_grace
            )
        except Exception as exc:  # advisory: the finalize below is the guarantee
            logger.debug("Muse MSP interrupt after timeout failed: %r", exc)
        try:
            await asyncio.wait_for(asyncio.shield(state.future), timeout=self._interrupt_grace)
        except TimeoutError:
            await self._finalize_stranded_turn(state, "timeout")
        except Exception:
            return

    async def _submit_turn(
        self,
        text: str,
        *,
        if_busy: str | None,
        msg_id: str | None = None,
        request_id: str | None = None,
    ) -> _TurnState | None:
        """Submit user input; returns the turn to wait on, or None when it was absorbed
        (steered into the running turn, or delivered as a clarification to an open question)."""
        if not text:
            return None
        await self._ensure_started()
        correlation = (msg_id, request_id)
        if self._pending_user_input is not None and if_busy == "steer":
            if await self._clarify_pending_user_input(text, correlation):
                return None
        command_id = _uuid7()
        params: dict[str, Any] = {
            "sessionId": self._muse_session_id,
            "commandId": command_id,
            "input": [{"type": "text", "text": text}],
        }
        if if_busy:
            params["ifBusy"] = if_busy
        if self._reasoning_effort:
            params["reasoningEffort"] = self._reasoning_effort
        # Register the turn BEFORE the request: a fresh (or queued) turn's id IS the
        # commandId (tdd SS3.1.4), and the reader may fold turn/started — even
        # turn/completed — before this coroutine resumes with the ack. A state created
        # after the fact would be a second, never-completing turn.
        state = self._ensure_turn(command_id)
        state.correlation = correlation
        state.in_chars += len(text)
        try:
            try:
                await self._ensure_session_loaded()
                ack = await self._request("turn/start", params, timeout=self._handshake_timeout)
            except MuseProtocolError as exc:
                if exc.kind not in ("sessionNotLoaded", "sessionNotFound"):
                    raise
                # The host dropped the session between turns (idle unload): reload once, retry.
                self._session_loaded = False
                await self._ensure_session_loaded()
                ack = await self._request("turn/start", params, timeout=self._handshake_timeout)
        except BaseException:
            if not state.started and not state.future.done():
                self._turns.pop(command_id, None)
            raise
        disposition = str(
            ack.get("disposition") or ("started" if ack.get("startedNewTurn") else "queued")
        )
        turn_id = str(ack.get("turnId") or command_id)
        if disposition == "steered":
            # Native mid-turn steer: the text joined the RUNNING turn as a steered user
            # message; there is no new turn and no turn/started to wait for.
            logger.info("Muse MSP steered the running turn %s", turn_id)
            self._turns.pop(command_id, None)
            active = self._turns.get(turn_id)
            if active is not None:
                active.in_chars += len(text)
            await self._emit_user_consumed(correlation)
            return None
        if turn_id != command_id:
            # The ack is authoritative for the turn id (tdd SS3.2). A fresh turn's id IS its
            # commandId, so this is the defensive path: adopt the state the reader may
            # already hold for that id (or its already-delivered terminal) rather than
            # waiting on a state no terminal will ever name.
            self._turns.pop(command_id, None)
            existing = self._turns.get(turn_id)
            if existing is not None:
                existing.correlation = existing.correlation or state.correlation
                existing.in_chars += state.in_chars
                state = existing
            elif turn_id in self._recent_results and not state.future.done():
                state.turn_id = turn_id
                state.future.set_result(self._recent_results[turn_id])
                return state
            else:
                state.turn_id = turn_id
                self._turns[turn_id] = state
        if state.started:
            # turn/started raced ahead of this ack on the reader — flip the bubble now.
            await self._flip_consumed(state)
        if disposition == "started":
            self._start_watchdog(state)
        else:
            logger.info("Muse MSP queued turn %s behind the running one", turn_id)
        return state

    async def _await_turn(self, state: _TurnState) -> None:
        """Wait for a submitted turn's terminal. The watchdog guarantees resolution."""
        budget = self._prompt_timeout + self._interrupt_grace + self._handshake_timeout
        try:
            await asyncio.wait_for(asyncio.shield(state.future), timeout=budget)
        except TimeoutError:
            await self._finalize_stranded_turn(state, "timeout")
        except asyncio.CancelledError:
            # Torn down mid-turn (session stop/restart): close the turn before re-raising.
            await self._finalize_stranded_turn(state, "cancelled")
            raise
        except Exception as exc:
            await self._finalize_stranded_turn(state, "error", detail=repr(exc))

    async def send_message(
        self, content: str, *, msg_id: str | None = None, request_id: str | None = None
    ) -> None:
        """Submit a user turn and wait for it to end (queued behind a running one)."""
        async with self._send_lock:
            state = await self._submit_turn(
                content, if_busy="queue", msg_id=msg_id, request_id=request_id
            )
            if state is None:
                return
            await self._await_turn(state)

    async def _interrupt_turn(self, turn_id: str | None, *, reason: str) -> None:
        params: dict[str, Any] = {}
        if turn_id:
            params["turnId"] = turn_id
        try:
            await self._command("turn/interrupt", params)
            logger.info(
                "Muse MSP interrupt admitted (%s, turn=%s)", reason, turn_id or "foreground"
            )
        except MuseProtocolError as exc:
            if exc.kind == "commandRejected":
                logger.debug("Muse MSP interrupt rejected (%s): nothing running", exc.reason)
                return
            raise

    async def interrupt(self) -> None:
        await self._interrupt_turn(self._active_turn_id, reason="interrupt")

    async def _finalize_stranded_turn(
        self, state: _TurnState, reason: str, detail: str = ""
    ) -> None:
        """Emit a terminal result so a turn can never be left open."""
        if state.future.done():
            return
        message = {
            "timeout": (
                f"The model stopped responding for {self._prompt_timeout:.0f}s and the turn was "
                "cancelled. Any tool calls shown without a result never completed. "
                "Send another message to continue."
            ),
            "cancelled": (
                "The session was stopped while this turn was running, so it was ended. "
                "Any tool calls shown without a result never completed."
            ),
            "error": (
                "The turn ended unexpectedly and was closed so the session stays usable."
                + (f" ({detail})" if detail else "")
            ),
        }.get(reason, "The turn was ended.")
        result = self._make_result(
            state,
            stop_reason=reason,
            text=message if not state.text_parts else "",
            is_error=True,
            error=message,
            usage=None,
        )
        await self._close_turn(state, result)

    async def _close_turn(self, state: _TurnState, result: dict) -> None:
        self._last_result = result
        try:
            await self._emit(result)
        except Exception as exc:  # pragma: no cover - teardown races
            logger.debug("Muse MSP could not emit the result: %r", exc)
        if not state.future.done():
            state.future.set_result(result)
        self._recent_results[state.turn_id] = result
        while len(self._recent_results) > _RECENT_RESULTS:
            self._recent_results.popitem(last=False)
        if (
            state.watchdog is not None
            and not state.watchdog.done()
            and state.watchdog is not asyncio.current_task()
        ):
            state.watchdog.cancel()
        self._turns.pop(state.turn_id, None)
        if self._active_turn_id == state.turn_id:
            self._active_turn_id = None
        for item_id in [
            i for i, info in self._items.items() if info.get("turn_id") == state.turn_id
        ]:
            self._items.pop(item_id, None)
        pending = self._pending_user_input
        if pending is not None and pending.get("turnId") == state.turn_id:
            self._pending_user_input = None
            await self._emit_ask_user_resolved(str(pending.get("userInputId") or ""), "cancelled")

    # ------------------------------------------------------------------
    # Result synthesis
    # ------------------------------------------------------------------

    def _model_usage(self, state: _TurnState, usage: dict | None) -> dict:
        model_id = state.usage_model or self._model
        if state.prompt_tokens or state.output_tokens:
            prompt, cached, out = state.prompt_tokens, state.cached_tokens, state.output_tokens
            uncached = max(prompt - cached, 0)
        elif isinstance(usage, dict) and (usage.get("inputTokens") or usage.get("outputTokens")):
            inp = int(usage.get("inputTokens") or 0)
            cached = int(usage.get("cachedTokens") or 0)
            out = int(usage.get("outputTokens") or 0)
            # cachedTokens sits inside or beside inputTokens depending on the provider
            # convention; if it exceeds the input count it was beside.
            uncached = inp if cached > inp else inp - cached
        else:
            # Nothing reported (a turn that never reached the model): estimate so the
            # broker still advances message_count/usage.
            uncached = max(1, state.in_chars // _CHARS_PER_TOKEN)
            cached = 0
            out = max(1, (state.out_chars + state.reason_chars) // _CHARS_PER_TOKEN)
        entry: dict[str, Any] = {
            "inputTokens": uncached,
            "outputTokens": out,
            "cacheReadInputTokens": cached,
            "cacheCreationInputTokens": 0,
        }
        price = _MODEL_PRICES_USD_PER_MTOK.get(model_id)
        if price is not None:
            in_rate, cache_rate, out_rate = price
            entry["costUSD"] = (
                uncached * in_rate + cached * cache_rate + out * out_rate
            ) / _TOKENS_PER_MILLION
        return {model_id: entry}

    def _make_result(
        self,
        state: _TurnState,
        *,
        stop_reason: str,
        text: str,
        is_error: bool = False,
        error: str | None = None,
        usage: dict | None = None,
        duration_ms: int | None = None,
        reason: str | None = None,
    ) -> dict:
        result: dict[str, Any] = {
            "type": "result",
            "stop_reason": stop_reason,
            "modelUsage": self._model_usage(state, usage),
            "sessionId": self._muse_session_id,
            "turnId": state.turn_id,
            "result": text or "",
        }
        if duration_ms is None:
            duration_ms = int((time.monotonic() - state.started_mono) * 1000)
        result["duration_ms"] = duration_ms
        if is_error:
            result["is_error"] = True
            result["subtype"] = f"error_{stop_reason}"
        if error:
            result["error"] = error
        if reason:
            result["reason"] = reason
        return result

    # ------------------------------------------------------------------
    # Notifications (the view stream)
    # ------------------------------------------------------------------

    async def _on_notification(self, method: str, params: dict) -> None:
        match method:
            case "turn/started":
                await self._on_turn_started(params)
            case "item/started":
                await self._on_item_started(params)
            case "item/delta":
                await self._on_item_delta(params)
            case "item/updated":
                await self._on_item_updated(params)
            case "item/completed":
                await self._on_item_completed(params)
            case "turn/completed":
                await self._on_turn_completed(params)
            case "turn/unqueued":
                await self._on_turn_unqueued(params)
            case "turn/retryScheduled":
                logger.info(
                    "Muse MSP retry scheduled for turn %s: %s",
                    params.get("turnId"),
                    params.get("reason") or params.get("error"),
                )
            case "session/tokenUsage":
                self._on_token_usage(params)
            case "session/contextUsage":
                self._context_usage = dict(params)
            case "session/todoListChanged":
                await self._on_todo_list(params)
            case "session/modelChanged":
                await self._on_model_changed(params)
            case "session/closed":
                await self._on_session_closed(params)
            case "view/gap":
                self._spawn(self._on_view_gap(params), "muse-gap-fill")
            case "userInput/settled":
                await self._on_user_input_settled(params)
            case _:
                logger.debug("Muse MSP notification %s ignored", method)

    async def _on_turn_started(self, params: dict) -> None:
        turn_id = str(params.get("turnId") or "")
        if not turn_id:
            return
        state = self._ensure_turn(turn_id)
        state.started = True
        self._active_turn_id = turn_id
        await self._flip_consumed(state)
        self._start_watchdog(state)

    def _turn_for(self, params: dict, item: dict | None = None) -> _TurnState | None:
        turn_id = None
        if item is not None:
            turn_id = item.get("turnId")
        if not turn_id:
            turn_id = params.get("turnId")
        if turn_id:
            return self._turns.get(str(turn_id))
        if self._active_turn_id:
            return self._turns.get(self._active_turn_id)
        return None

    async def _on_item_started(self, params: dict) -> None:
        item = params.get("item") or {}
        item_id = str(item.get("itemId") or "")
        if not item_id:
            return
        kind = str(item.get("kind") or "")
        state = self._turn_for(params, item)
        info: dict[str, Any] = {"kind": kind, "turn_id": item.get("turnId"), "streamed": 0}
        self._items[item_id] = info
        match kind:
            case "agentMessage":
                text = str(item.get("text") or "")
                if text:
                    info["streamed"] = len(text)
                    await self._emit_text(state, text)
            case "reasoning":
                return
            case "toolCall":
                info.update(
                    {
                        "name": _map_muse_tool(str(item.get("tool") or "")),
                        "raw_tool": item.get("tool"),
                        "started_at": _utc_now_iso(),
                        "emitted": False,
                        "output": "",
                    }
                )
                if item.get("args") not in (None, ""):
                    await self._emit_tool_use(item_id, info, item)
            case "subagent":
                info.update({"name": "Task", "started_at": _utc_now_iso(), "emitted": True})
                block = {
                    "type": "tool_use",
                    "id": item_id,
                    "name": "Task",
                    "input": {
                        "role": item.get("role"),
                        "objective": item.get("objective"),
                        "agent_path": item.get("agentPath"),
                        "subagent_id": item.get("subagentId"),
                    },
                }
                await self._emit(self._assistant_event([block]))
            case "userMessage":
                return
            case _:
                await self._emit_passthrough(kind, item)

    async def _emit_tool_use(self, item_id: str, info: dict, item: dict) -> None:
        if info.get("emitted"):
            return
        info["emitted"] = True
        block: dict[str, Any] = {
            "type": "tool_use",
            "id": item_id,
            "name": info.get("name") or _map_muse_tool(str(item.get("tool") or "")),
            "input": _parse_args(item.get("args")),
        }
        await self._emit(self._assistant_event([block]))

    async def _on_item_delta(self, params: dict) -> None:
        item_id = str(params.get("itemId") or "")
        delta = str(params.get("delta") or "")
        field_path = str(params.get("field") or "text")
        if not item_id or not delta:
            return
        info = self._items.get(item_id)
        if info is None:
            # A delta for an item whose open we never saw (attached mid-stream / after a
            # gap): infer the kind from the field path so text is not lost.
            kind = (
                "reasoning"
                if field_path.startswith("summary")
                else "agentMessage"
                if field_path == "text"
                else ""
            )
            if not kind:
                return
            info = {"kind": kind, "turn_id": None, "streamed": 0}
            self._items[item_id] = info
        state = self._turn_for(params, {"turnId": info.get("turn_id")})
        kind = info.get("kind")
        if kind == "agentMessage" and field_path == "text":
            info["streamed"] = int(info.get("streamed") or 0) + len(delta)
            await self._emit_text(state, delta)
        elif kind == "reasoning" and (field_path.startswith("summary") or field_path == "text"):
            info["streamed"] = int(info.get("streamed") or 0) + len(delta)
            await self._emit_thinking(state, delta)
        elif kind == "toolCall" and field_path == "output":
            info["output"] = str(info.get("output") or "") + delta

    async def _on_item_updated(self, params: dict) -> None:
        item = params.get("item") or {}
        item_id = str(item.get("itemId") or "")
        info = self._items.get(item_id)
        if info is None or info.get("kind") != "toolCall":
            return
        if not info.get("emitted") and item.get("args") not in (None, ""):
            await self._emit_tool_use(item_id, info, item)

    async def _on_item_completed(self, params: dict) -> None:
        item = params.get("item") or {}
        item_id = str(item.get("itemId") or "")
        if not item_id:
            return
        kind = str(item.get("kind") or "")
        info = self._items.pop(item_id, None) or {
            "kind": kind,
            "turn_id": item.get("turnId"),
            "streamed": 0,
        }
        state = self._turn_for(params, item)
        status = str(item.get("status") or "completed")
        match kind:
            case "agentMessage":
                text = str(item.get("text") or "")
                streamed = int(info.get("streamed") or 0)
                if len(text) > streamed:
                    await self._emit_text(state, text[streamed:])
            case "reasoning":
                if int(info.get("streamed") or 0) == 0:
                    body = str(item.get("text") or "") or "\n\n".join(
                        str(s) for s in (item.get("summary") or []) if s
                    )
                    await self._emit_thinking(state, body)
            case "toolCall":
                if not info.get("emitted"):
                    info.setdefault("name", _map_muse_tool(str(item.get("tool") or "")))
                    await self._emit_tool_use(item_id, info, item)
                await self._emit_tool_result(item_id, info, item, status)
            case "subagent":
                result = item.get("result")
                content = (
                    json.dumps(result, ensure_ascii=False)
                    if isinstance(result, dict | list)
                    else str(
                        result or item.get("fallbackText") or item.get("failureReason") or status
                    )
                )
                await self._emit_tool_result(
                    item_id, info, {"visibleOutput": content, **item}, status
                )
            case "userMessage":
                return
            case "compaction":
                await self._emit(
                    {
                        "type": "system",
                        "subtype": "compact_boundary",
                        "compact_metadata": {
                            "trigger": item.get("trigger") or "auto",
                            "pre_tokens": item.get("tokensBefore"),
                            "post_tokens": item.get("tokensAfter"),
                            "outcome": item.get("outcome"),
                        },
                        "content": [{"type": "compaction", **item}],
                    }
                )
            case _:
                await self._emit_passthrough(kind, item)

    async def _emit_tool_result(self, item_id: str, info: dict, item: dict, status: str) -> None:
        payload = item.get("visibleOutput")
        if payload in (None, ""):
            payload = info.get("output") or item.get("failureReason") or ""
        if item.get("truncated"):
            payload = f"{payload}\n… [output truncated by Muse; full text is in the session log]"
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": item_id,
            "content": str(payload),
        }
        if status in _ERROR_ITEM_STATUSES:
            block["is_error"] = True
            reason = item.get("failureReason") or item.get("failureKind")
            if reason and reason not in str(payload):
                block["content"] = f"{payload}\n[{status}: {reason}]".strip()
        # D1 per-tool timing — the stamp hierarchical rows read for "ran in 3m 12s".
        block[TOOL_ENDED_AT] = _utc_now_iso()
        if info.get("started_at"):
            block["started_at"] = info["started_at"]
        # ROLE IS LOAD-BEARING: a tool_result rides in a user frame (the reducer only
        # harvests tool_result blocks from `user` frames).
        await self._emit(self._user_event([block]))

    async def _emit_passthrough(self, kind: str, item: dict) -> None:
        if not kind:
            return
        await self._emit(
            {"type": "system", "subtype": f"muse.{kind}", "content": [{"type": kind, **item}]}
        )

    async def _on_turn_completed(self, params: dict) -> None:
        turn_id = str(params.get("turnId") or "")
        if not turn_id:
            return
        state = self._turns.get(turn_id)
        if state is None:
            # A terminal for a turn we never submitted (another client on the same session,
            # or a resumed one): still fold it so usage is not lost, but emit no result —
            # the broker has no open turn to close.
            logger.info(
                "Muse MSP turn %s completed (not ours): %s", turn_id, params.get("terminal")
            )
            return
        terminal = str(params.get("terminal") or "completed")
        stop_reason = _STOP_REASON_BY_TERMINAL.get(terminal, terminal)
        error = params.get("error") if isinstance(params.get("error"), dict) else None
        text = "".join(state.text_parts)
        is_error = terminal == "failed"
        error_text: str | None = None
        if is_error:
            kind = str((error or {}).get("kind") or "modelError")
            message = str((error or {}).get("message") or params.get("reason") or "unknown failure")
            retry = " (retryable)" if (error or {}).get("retryable") else ""
            error_text = f"Muse turn failed ({kind}){retry}: {message}"
            if not text:
                text = error_text
        result = self._make_result(
            state,
            stop_reason=stop_reason,
            text=text,
            is_error=is_error,
            error=error_text,
            usage=params.get("usage") if isinstance(params.get("usage"), dict) else None,
            duration_ms=int(params["durationMs"])
            if isinstance(params.get("durationMs"), int | float)
            else None,
            reason=str(params.get("reason")) if params.get("reason") else None,
        )
        if isinstance(params.get("timeToFirstTokenMs"), int | float):
            result["time_to_first_token_ms"] = int(params["timeToFirstTokenMs"])
        await self._close_turn(state, result)

    async def _on_turn_unqueued(self, params: dict) -> None:
        turn_id = str(params.get("turnId") or "")
        state = self._turns.pop(turn_id, None)
        if state is None:
            return
        logger.info("Muse MSP queued turn %s was reclaimed before it ran", turn_id)
        if not state.future.done():
            state.future.set_result(
                {"type": "result", "stop_reason": "unqueued", "turnId": turn_id}
            )
        if state.watchdog is not None and not state.watchdog.done():
            state.watchdog.cancel()

    def _on_token_usage(self, params: dict) -> None:
        turn_id = str(params.get("turnId") or "")
        usage = params.get("usage") if isinstance(params.get("usage"), dict) else {}
        state = self._turns.get(turn_id) if turn_id else None
        if state is not None:
            state.prompt_tokens += int(params.get("promptTokens") or usage.get("inputTokens") or 0)
            state.cached_tokens += int(usage.get("cachedTokens") or 0)
            state.output_tokens += int(usage.get("outputTokens") or 0)
            state.reasoning_tokens += int(usage.get("reasoningTokens") or 0)
            if params.get("modelId"):
                state.usage_model = str(params["modelId"])
        if isinstance(params.get("cumulative"), dict):
            self._cumulative_usage = dict(params["cumulative"])

    async def _on_todo_list(self, params: dict) -> None:
        items = params.get("items") if isinstance(params.get("items"), list) else []
        tasks: list[dict] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "pending")
            status = {"inProgress": "in_progress"}.get(status, status)
            task = {
                "content": str(entry.get("content") or entry.get("text") or ""),
                "status": status,
            }
            if entry.get("id"):
                task["id"] = str(entry["id"])
            tasks.append(task)
        counts = {"total": len(tasks)}
        for status in ("pending", "in_progress", "completed"):
            counts[status] = sum(1 for t in tasks if t["status"] == status)
        self._current_plan = tasks
        await self._emit(
            {
                "type": "plan",
                "event_type": "muse.plan",
                "tasks": tasks,
                "counts": counts,
                "metadata": {"source": "muse_msp"},
            }
        )

    async def _on_model_changed(self, params: dict) -> None:
        model = params.get("model") if isinstance(params.get("model"), dict) else params
        model_id = str((model or {}).get("modelId") or "")
        if model_id and model_id != self._model:
            logger.info("Muse MSP model changed: %s -> %s", self._model, model_id)
            self._model = model_id

    async def _on_session_closed(self, params: dict) -> None:
        reason = str(params.get("reason") or "unknown")
        logger.info("Muse MSP session %s closed by the host (%s)", params.get("sessionId"), reason)
        self._session_loaded = False
        if reason == "hostShutdown":
            return
        for turn_id in list(self._turns):
            state = self._turns.get(turn_id)
            if state is not None and not state.future.done():
                await self._finalize_stranded_turn(
                    state, "error", detail=f"session closed: {reason}"
                )

    async def _on_view_gap(self, params: dict) -> None:
        """Splice-fill a dropped delivery (tdd SS4.8): page (after, next) and fold it in.

        Deltas lost in the hole stay lost by design (pages carry final item states), which
        is exactly why item/completed re-emits the full text and the fold tops it up.
        """
        after = params.get("after")
        next_cursor = params.get("next")
        session_id = params.get("sessionId") or self._muse_session_id
        logger.warning("Muse MSP view gap (%s, %s) — paging the hole", after, next_cursor)
        cursor = after
        for _ in range(_GAP_MAX_PAGES):
            page_params: dict[str, Any] = {
                "sessionId": session_id,
                "direction": "forward",
                "limit": _GAP_PAGE_LIMIT,
            }
            if cursor:
                page_params["cursor"] = cursor
            try:
                page = await self._request(
                    "view/page", page_params, timeout=self._handshake_timeout
                )
            except Exception as exc:
                logger.warning("Muse MSP gap fill failed: %r", exc)
                return
            for event in page.get("events") or []:
                if not isinstance(event, dict):
                    continue
                ev_params = event.get("params") or {}
                if next_cursor and ev_params.get("viewCursor") == next_cursor:
                    return
                await self._on_notification(str(event.get("method") or ""), ev_params)
            cursor = page.get("nextCursor")
            if not cursor:
                return

    # ------------------------------------------------------------------
    # Server requests: approvals and questions
    # ------------------------------------------------------------------

    async def _on_server_request(self, req_id: Any, method: str, params: dict) -> None:
        if method == "approval/request":
            await self._respond(req_id, result={})
            self._spawn(self._on_approval_request(params), "muse-approval")
            return
        if method == "userInput/request":
            await self._respond(req_id, result={})
            await self._on_user_input_request(params)
            return
        logger.warning(
            "Muse MSP requested %s, which this client does not implement — refusing so the "
            "host falls back instead of blocking",
            method,
        )
        await self._respond(
            req_id, error={"code": -32601, "message": f"client does not implement {method}"}
        )

    async def _decide_approval(
        self, approval_id: str, requirement: dict, choice: dict, feedback: str | None
    ) -> None:
        params: dict[str, Any] = {
            "approvalId": approval_id,
            "requirementId": requirement,
            "choiceId": choice.get("choiceId"),
        }
        if feedback and choice.get("acceptsFeedback"):
            params["feedback"] = _clip(feedback)
        try:
            await self._command("approval/decide", params)
        except MuseProtocolError as exc:
            logger.warning("Muse MSP approval %s could not be decided: %s", approval_id, exc)

    async def _on_approval_request(self, params: dict) -> None:
        approval_id = str(params.get("approvalId") or "")
        requirement = (
            params.get("currentRequirementId")
            if isinstance(params.get("currentRequirementId"), dict)
            else {}
        )
        choices = params.get("availableChoices") or []
        tool = _map_muse_tool(str(params.get("toolName") or ""))
        if self._skip_permissions:
            choice = _pick_choice(choices, "allowForever")
            if choice is None:
                logger.warning(
                    "Muse MSP approval %s offered no approve choice — leaving it to the host",
                    approval_id,
                )
                return
            logger.info(
                "Muse MSP auto-approving %s (%s) with %s", tool, approval_id, choice.get("choiceId")
            )
            await self._decide_approval(approval_id, requirement, choice, None)
            return
        # Human gate: surface it as the permission control_request the clients already render.
        request_id = f"{approval_id}:{requirement.get('sourceIndex', 0)}"
        self._pending_approvals[request_id] = {
            "approvalId": approval_id,
            "requirementId": requirement,
            "choices": choices,
        }
        tool_input: Any = _parse_args(params.get("rawArgs"))
        if not isinstance(tool_input, dict):
            tool_input = {"input": tool_input}
        subject = _subject_summary(params.get("subject"))
        if subject.get("command") and "command" not in tool_input:
            tool_input["command"] = subject["command"]
        if subject.get("path") and "path" not in tool_input and "file_path" not in tool_input:
            tool_input["file_path"] = subject["path"]
        await self._emit(
            {
                "type": "control_request",
                "subtype": "can_use_tool",
                "request_id": request_id,
                "tool": tool,
                "input": tool_input,
                "muse": {
                    "subject": subject,
                    "protected_write": bool(params.get("protectedWrite")),
                    "judge_escalated": bool(params.get("judgeEscalated")),
                    "choices": [
                        {
                            "choiceId": c.get("choiceId"),
                            "label": c.get("label"),
                            "scope": c.get("scope"),
                        }
                        for c in choices
                        if isinstance(c, dict)
                    ],
                },
            }
        )

    async def send_control_response(self, request_id: str, response: dict) -> None:
        """Answer a surfaced approval with the broker's permission response."""
        pending = self._pending_approvals.pop(str(request_id), None)
        if pending is None:
            logger.debug("Muse MSP control response for unknown request_id=%s ignored", request_id)
            return
        behavior = str((response or {}).get("behavior") or "allow")
        if behavior not in ("allow", "allowForever", "deny"):
            behavior = "allow" if "allow" in behavior.lower() else "deny"
        choice = _pick_choice(pending["choices"], behavior)
        if choice is None:
            logger.warning(
                "Muse MSP approval %s has no %s choice — leaving it to the host",
                pending["approvalId"],
                behavior,
            )
            return
        feedback = str((response or {}).get("message") or "") if behavior == "deny" else None
        await self._decide_approval(
            pending["approvalId"], pending["requirementId"], choice, feedback
        )

    async def _on_user_input_request(self, params: dict) -> None:
        user_input_id = str(params.get("userInputId") or "")
        questions = [q for q in (params.get("questions") or []) if isinstance(q, dict)]
        self._pending_user_input = {
            "userInputId": user_input_id,
            "questions": questions,
            "toolCallId": params.get("toolCallId"),
            "itemId": params.get("itemId"),
            "turnId": params.get("turnId"),
        }
        # `event_type` makes the broker flip to awaiting_input; `type` is what the clients
        # switch on. Carry both (mirrors the tmux bridge).
        await self._emit(
            {
                "type": "ask_user_question",
                "event_type": "ask_user_question",
                "request_id": user_input_id,
                "tool_use_id": params.get("itemId") or params.get("toolCallId") or user_input_id,
                "questions": _claude_questions(questions),
                "metadata": {"source": "muse_msp"},
            }
        )

    async def _emit_ask_user_resolved(self, request_id: str, decision: str) -> None:
        await self._emit(
            {
                "type": "ask_user_resolved",
                "event_type": "ask_user.resolved",
                "request_id": request_id,
                "decision": decision,
                "accepted": decision in {"answered", "clarified"},
                "metadata": {"source": "muse_msp"},
            }
        )

    async def _answer_user_input(self, request_id: str, answers: Any) -> None:
        pending = self._pending_user_input
        if pending is None or (request_id and pending.get("userInputId") not in (request_id, "")):
            logger.warning("Muse MSP ask_user_answer for unknown request_id=%s ignored", request_id)
            return
        user_input_id = str(pending.get("userInputId") or "")
        questions = pending.get("questions") or []
        try:
            await self._command(
                "userInput/answer",
                {"userInputId": user_input_id, "answers": _msp_answers(questions, answers)},
            )
        except MuseProtocolError as exc:
            if exc.kind != "userInputAnswerInvalid":
                logger.warning("Muse MSP could not answer question %s: %s", user_input_id, exc)
                return
            # Shape drift between what the client sent and what the prompt accepts: hand the
            # answers to the model as a clarification instead of dropping them.
            wire_answers = _msp_answers(questions, answers)
            lines = []
            for i, (q, a) in enumerate(zip(questions, wire_answers, strict=False)):
                label = q.get("header") or q.get("question") or str(i + 1)
                value = (
                    a.get("selectedLabel")
                    or ", ".join(a.get("selectedLabels") or [])
                    or a.get("freeText")
                    or ""
                )
                lines.append(f"{label}: {value}")
            summary = "; ".join(lines)
            await self._command(
                "userInput/clarify",
                {
                    "userInputId": user_input_id,
                    "clarification": {"format": "text", "content": _clip(summary)},
                },
            )
        self._pending_user_input = None
        await self._emit_ask_user_resolved(user_input_id, "answered")

    async def _clarify_pending_user_input(
        self, text: str, correlation: tuple[str | None, str | None]
    ) -> bool:
        """A chat message while the agent waits on a question is the answer ("let me explain")."""
        pending = self._pending_user_input
        if pending is None:
            return False
        user_input_id = str(pending.get("userInputId") or "")
        try:
            if len(text) <= _USER_INPUT_TEXT_LIMIT:
                await self._command(
                    "userInput/clarify",
                    {
                        "userInputId": user_input_id,
                        "clarification": {"format": "text", "content": text},
                    },
                )
                decision = "clarified"
            else:
                # Too long for a clarification: decline the prompt and let the caller steer
                # the full text into the turn instead.
                await self._command(
                    "userInput/cancel",
                    {"userInputId": user_input_id, "reason": "the user replied in chat instead"},
                )
                self._pending_user_input = None
                await self._emit_ask_user_resolved(user_input_id, "cancelled")
                return False
        except MuseProtocolError as exc:
            logger.warning(
                "Muse MSP could not clarify question %s: %s — steering instead", user_input_id, exc
            )
            self._pending_user_input = None
            return False
        self._pending_user_input = None
        await self._emit_ask_user_resolved(user_input_id, decision)
        await self._emit_user_consumed(correlation)
        return True

    async def _on_user_input_settled(self, params: dict) -> None:
        pending = self._pending_user_input
        user_input_id = str(params.get("userInputId") or "")
        if pending is not None and pending.get("userInputId") == user_input_id:
            self._pending_user_input = None
            await self._emit_ask_user_resolved(
                user_input_id, str(params.get("outcome") or "answered")
            )

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    async def get_effort(self) -> dict:
        return {
            "current": self._reasoning_effort or "",
            "levels": list(MODEL_EFFORTS.get(self._model, ())),
            "mutable": True,
            "applies_to": "next_submission",
        }

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        """Server-initiated controls: interrupt, native steer, question answers, model/approval."""
        if subtype == "set_effort":
            self._reasoning_effort = validate_effort(
                str(kwargs.get("effort") or ""), (await self.get_effort())["levels"]
            )
            return

        if subtype == "interrupt":
            logger.info("MuseMSPTransport: received interrupt control")
            await self._interrupt_turn(self._active_turn_id, reason="interrupted by control")
            return

        if subtype in {"redirect", "steer", "steer_active_turn"}:
            content = kwargs.get("content")
            text = str(content) if content is not None else ""
            if not text:
                return
            raw_msg_id = kwargs.get("msg_id")
            raw_request_id = kwargs.get("request_id")
            msg_id = raw_msg_id if isinstance(raw_msg_id, str) and raw_msg_id else None
            request_id = (
                raw_request_id if isinstance(raw_request_id, str) and raw_request_id else None
            )
            # NATIVE steering: `ifBusy: steer` joins a running turn without interrupting it,
            # and simply starts a turn when idle. Only the admission ack is awaited.
            await self._submit_turn(text, if_busy="steer", msg_id=msg_id, request_id=request_id)
            return

        if subtype == "ask_user_answer":
            await self._answer_user_input(
                str(kwargs.get("request_id") or ""), kwargs.get("answers")
            )
            return

        if subtype == "set_model":
            model = _resolve_model(str(kwargs.get("model") or ""))
            if not model or model == self._model:
                return
            await self._command("session/setModel", {"model": {"modelId": model}})
            self._model = model
            logger.info("MuseMSPTransport: model set to %s", model)
            return

        if subtype == "set_permission_mode":
            mode = _resolve_approval_mode(
                str(kwargs.get("mode") or ""), default=self._approval_mode
            )
            await self._command("session/setApprovalMode", {"mode": mode})
            self._approval_mode = mode
            logger.info("MuseMSPTransport: approval mode set to %s", mode)
            return

        logger.debug("MuseMSPTransport.send_control(%s, %s) — no-op", subtype, kwargs)

    # ------------------------------------------------------------------
    # Properties required by CLITransport + broker
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        return self._muse_session_id

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
    def is_turn_active(self) -> bool:
        """True while any submitted turn has not reached its terminal."""
        return any(not s.future.done() for s in self._turns.values())

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            send_message=True,
            cli_websocket=False,
            session_resume=True,
            interrupt=True,
            set_effort=True,
            # MSP `turn/start ifBusy: steer` injects input into the RUNNING turn — the CLI
            # absorbs it itself, nothing is interrupted — so steering is native and the
            # broker routes every delivery through `redirect` with a correlation id.
            steer=True,
            steering_mode="native",
            set_model=True,
            set_permission_mode=True,
            permission_requests=not self._skip_permissions,
            skills=True,  # Muse Code surfaces skills, subagents and workflows as tools
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def approval_mode(self) -> str:
        return self._approval_mode

    @property
    def current_plan(self) -> list[dict]:
        return list(self._current_plan)


__all__ = [
    "MUSE_DEFAULT_MODEL",
    "MuseMSPTransport",
    "MuseProtocolError",
    "_MUSE_TOOL_MAP",
    "_map_muse_tool",
]
