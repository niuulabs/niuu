"""GrokACPTransport — xAI Grok Build via Agent Client Protocol (ACP) over stdio.

Uses the recommended ACP engine path (`grok agent stdio`) for rich integration:
persistent agent process, streaming thoughts, tool visibility, plans, and
structured session control. Events are normalized to the same Claude-style
format used by other Skuld transports so the broker, Ravn, Volundr UI,
artifact tracking, and usage reporting continue to work unchanged.

Authentication: relies on the grok CLI (XAI_API_KEY or cached auth.json).
Install via the official xAI CLI installer so `grok` is on PATH inside the
Skuld container/pod (or mount the binary).

Model ids come from `grok models` — currently "grok-4.6" (default) and "grok-4.5".
Pass via Skuld session.model. An unknown id is fatal: the CLI answers
`Couldn't set model '<id>': Invalid params: "unknown model id"` and exits non-zero.
"grok-build" was such an id for months, which is why every Grok session died on
its first prompt; the id is validated against `grok models` by the E2E suite.
"""

import asyncio
import json
import logging
import os
import shutil
import signal
from datetime import UTC, datetime
from typing import Any

from niuu.domain.reasoning import validate_effort
from niuu.domain.transcript_reducer import TOOL_ENDED_AT
from skuld.transports import (
    CLITransport,
    TransportCapabilities,
    _drain_stream,
    _filter_event,
    _stop_process,
)

logger = logging.getLogger("skuld.transport")


def _utc_now_iso() -> str:
    """UTC timestamp in the shape the transcript reducer expects for tool timing."""
    return datetime.now(UTC).isoformat()


# Grok ACP surfaces no token counts, so usage is estimated from streamed text at
# this rough ratio (~4 chars/token) — enough for message_count/usage to advance.
_CHARS_PER_TOKEN = 4

# Default model id. MUST be one of `grok models`; an unknown id kills the session at
# its first prompt. Overridden per session via Skuld session.model.
GROK_DEFAULT_MODEL = "grok-4.6"

# Model ids clients may still be sending. A client is deployed separately from this
# server — an iOS build in the field keeps sending whatever id it shipped with — so a
# retired id must not kill the session. `grok-build` in particular was this project's
# id for months and is baked into every app build before 2227; it was never a real
# `grok models` entry, so the CLI rejects it and the session dies at its first prompt
# with nothing in the UI to explain why (observed live: session `lexi-frontend-voice`,
# 2026-08-16, message_count 0 and never active).
#
# Aliasing is the right shape for this: the server knows the current catalogue, the
# client cannot, and silently upgrading a retired id is strictly better than failing.
# Logged loudly so the skew is visible rather than papered over.
_LEGACY_MODEL_ALIASES: dict[str, str] = {
    "grok-build": GROK_DEFAULT_MODEL,
    "grok-4": GROK_DEFAULT_MODEL,
    "grok": GROK_DEFAULT_MODEL,
    "": GROK_DEFAULT_MODEL,
}


def _resolve_model(model: str | None) -> str:
    """Map a retired/blank model id onto a current one; pass real ids through."""
    raw = (model or "").strip()
    alias = _LEGACY_MODEL_ALIASES.get(raw.lower())
    if alias:
        if raw:
            logger.warning(
                "Grok model %r is retired — using %r instead. The caller is on an old "
                "build; update it so the id it sends is real.",
                raw,
                alias,
            )
        return alias
    return raw


# ---------------------------------------------------------------------------
# Tool name mapping — Grok internal IDs -> normalized names (for UI parity)
# ---------------------------------------------------------------------------
#
# Grok 1.0.x serves tools from TWO namespaces, visible in each tool_call's
# ``_meta["x.ai/tool"]``:
#   * ``grok_build``  — run_terminal_command, todo_write, …  (Grok's own)
#   * ``opencode``    — write, read, edit, …                 (the opencode set)
# A fixed map alone therefore goes stale every time either side adds a tool, and
# in 1.0.4 the opencode names were already unmapped (a Write rendered as "write").
#
# So the map is now an OVERRIDE for the names we want spelled the Claude way — it
# is what keeps Bash/Edit/Read identical across engines, which the hierarchical
# row classifier keys on — and anything unmapped falls back to the CLI's OWN
# ``label`` before the raw name. New Grok tools then arrive with a sensible title
# instead of a snake_case identifier.

_GROK_TOOL_MAP: dict[str, str] = {
    # grok_build namespace
    "run_terminal_command": "Bash",
    "search_replace": "Edit",
    "read_file": "Read",
    "list_dir": "LS",
    "grep": "Grep",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "todo_write": "TodoWrite",
    "spawn_subagent": "Task",
    "memory_search": "Memory",
    # opencode namespace (1.0.x) — these were entirely unmapped
    "bash": "Bash",
    "write": "Write",
    "read": "Read",
    "edit": "Edit",
    "list": "LS",
    "glob": "Glob",
    "patch": "Edit",
    "todowrite": "TodoWrite",
    "task": "Task",
    "webfetch": "WebFetch",
}


def _map_grok_tool(name: str, label: str | None = None) -> str:
    """Normalize a Grok tool name for UI parity.

    Order: explicit map (cross-engine canonical spelling) → the CLI's own
    ``label`` → the raw name. ``label`` comes from ``_meta["x.ai/tool"].label``.
    """
    key = (name or "").strip()
    mapped = _GROK_TOOL_MAP.get(key) or _GROK_TOOL_MAP.get(key.lower())
    if mapped:
        return mapped
    if label and label.strip():
        return label.strip()
    return key or "tool"


def _grok_tool_meta(update: dict) -> dict:
    """The ``x.ai/tool`` descriptor on a tool_call update (name/kind/label/read_only)."""
    meta = update.get("_meta") or {}
    tool = meta.get("x.ai/tool") if isinstance(meta, dict) else None
    return tool if isinstance(tool, dict) else {}


# ACP tool_call statuses that mean the call is FINISHED (so its result can be
# paired and its duration stamped). Anything else is progress.
_TERMINAL_TOOL_STATUSES = {"completed", "failed", "error", "cancelled", "canceled", "rejected"}
_ERROR_TOOL_STATUSES = {"failed", "error", "rejected"}


class GrokACPTransport(CLITransport):
    """Persistent ACP client over `grok agent stdio` (Scaldy / Grok Build pipeline).

    - Spawns one long-lived `grok agent --always-approve -m <model> stdio` process
      (agent-level flags MUST precede the `stdio` subcommand).
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
        model: str = GROK_DEFAULT_MODEL,
        session_id: str | None = None,
        grok_bin: str | None = None,
        skip_permissions: bool = True,
        agent_teams: bool = False,
        system_prompt: str = "",
        initial_prompt: str = "",
        reasoning_effort: str = "",
        acp_prompt_timeout_s: float = 300.0,
        acp_auth_preflight_timeout_s: float = 60.0,
        live_frame_max_bytes: int = 8 * 1024 * 1024,
        **_: Any,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = _resolve_model(model)
        self._requested_session_id = session_id
        self._grok_bin_override = grok_bin
        self._skip_permissions = skip_permissions  # yolo via --always-approve; accepted for parity
        self._agent_teams = agent_teams
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._reasoning_effort = reasoning_effort
        self._effort_options: dict = {}
        self._prompt_timeout = acp_prompt_timeout_s
        self._auth_preflight_timeout = acp_auth_preflight_timeout_s
        self._live_frame_max_bytes = live_frame_max_bytes

        self._process: asyncio.subprocess.Process | None = None
        # Set synchronously at the top of start() so a concurrent start cannot
        # slip past the guard while the auth preflight is awaited.
        self._starting = False
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
        # Steering follow-ups queued while a turn is in flight. ACP turns are
        # sequential (no native mid-turn input), so a steer interrupts the
        # current prompt and the send_message loop resumes with the queued text.
        self._pending_steers: list[str] = []

        self._pending_text_chunks: list[str] = []
        # Per-turn character counters for usage estimation (reset each turn).
        self._turn_out_chars: int = 0
        self._turn_reason_chars: int = 0
        self._turn_in_chars: int = 0
        self._stdout_reader: asyncio.StreamReader | None = None
        # toolCallId -> {name, started_at}: open ACP tool calls awaiting their terminal
        # update, so the result pairs to the right call and carries a real duration.
        self._open_tool_calls: dict[str, dict[str, Any]] = {}

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
        proc = None
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
        finally:
            if proc is not None and proc.returncode is None:
                await _stop_process(proc)

    async def start(self) -> None:
        logger.info(
            "GrokACPTransport configured for %s "
            "(model: %s, system_prompt=%d chars, initial_prompt=%d chars)",
            self.workspace_dir,
            self._model,
            len(self._system_prompt or ""),
            len(self._initial_prompt or ""),
        )

        # START-ONCE. The old guard was `if self._process is not None` — a check-then-act
        # across an await: `_preflight_auth` below can take up to 60 s, and `_process` is
        # only assigned after the spawn, so two concurrent start() calls both passed the
        # check, both waited out the preflight, and both spawned an agent. Two reader
        # loops then raced the same stdout, which is the
        # `readuntil() called while another coroutine is already waiting` RuntimeError
        # seen live (session `lexi-frontend-voice`, 2026-08-16 — "configured" and
        # "Spawning Grok ACP agent" each logged twice). The flag is set BEFORE any await,
        # so the second caller returns immediately.
        if self._starting or self._process is not None:
            logger.debug("GrokACPTransport.start() ignored — already starting/started")
            return
        self._starting = True

        try:
            await self._start_agent()
        except BaseException:
            # Cancellation is a failed start too. The prompt lock may already
            # be held by send_message(), so cleanup must not reacquire it.
            await self._teardown_process()
            raise

    async def _start_agent(self) -> None:

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
            limit=self._live_frame_max_bytes,
        )
        self._process = process

        if process.stdout is None or process.stdin is None:
            # Failed start must be retryable, not wedged in "starting".
            self._starting = False
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
            await self._teardown_process()

    async def _teardown_process(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        if self._process:
            await _stop_process(self._process)
        self._process = None
        self._starting = False
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
                if not isinstance(data, dict):
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

                # REQUEST FROM THE AGENT (has BOTH `method` and `id`).
                #
                # This must be tested BEFORE the response branch below: an inbound
                # request also carries an `id`, so it used to fall through, fail to
                # match any pending future, and get parked in `_early_results` —
                # where nobody ever answered it and the agent blocked until the
                # prompt timed out. Worse, the agent numbers its requests from its
                # own counter, so an agent id could collide with ours and wrongly
                # resolve one of OUR futures.
                #
                # Anything we cannot service is refused immediately. A clean
                # "unsupported" lets the agent fall back to doing the work itself;
                # silence is the one answer that hangs it.
                if data.get("method") and "id" in data:
                    await self._handle_agent_request(data)
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
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(RuntimeError("Grok ACP connection closed"))
            self._pending.clear()
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

        # Bounded wait so a silent/unresponsive agent can never hang the handshake.
        try:
            self._process.stdin.write(line.encode())
            await self._process.stdin.drain()

            early = self._early_results.pop(req_id, None)
            if early is not None:
                if "error" in early:
                    raise RuntimeError(early["error"])
                return early.get("result") or {}
            return await asyncio.wait_for(fut, timeout=self._prompt_timeout)
        finally:
            self._pending.pop(req_id, None)
            if not fut.done():
                fut.cancel()

    async def _handle_agent_request(self, data: dict) -> None:
        """Answer an agent->client JSON-RPC request. Never leave one unanswered.

        We advertise no client-side filesystem capability (see `_acp_initialize`), so
        in practice Grok should not ask. This exists because the failure mode when it
        does is invisible and total: the agent waits on a reply that never comes, the
        tool never completes, and the whole turn dies at the prompt timeout having
        done nothing. A prompt refusal is always better than silence — the agent can
        then fall back to its own implementation.

        `session/request_permission` is the one we answer affirmatively: the session
        runs with `--always-approve`, so a permission prompt has exactly one answer.
        """
        req_id = data.get("id")
        method = str(data.get("method") or "")

        if method == "session/request_permission":
            # Mirror the --always-approve contract. Pick the first "allow"-ish option
            # the agent offered so we answer in its own vocabulary.
            params = data.get("params") or {}
            options = params.get("options") or []
            chosen = None
            for opt in options:
                if isinstance(opt, dict) and "allow" in str(opt.get("kind", "")).lower():
                    chosen = opt.get("optionId") or opt.get("id")
                    break
            if chosen is None and options and isinstance(options[0], dict):
                chosen = options[0].get("optionId") or options[0].get("id")
            outcome = (
                {"outcome": "selected", "optionId": chosen}
                if chosen is not None
                else {"outcome": "cancelled"}
            )
            logger.debug("Grok ACP permission request auto-approved (%s)", chosen)
            await self._acp_respond(req_id, result={"outcome": outcome})
            return

        logger.warning(
            "Grok ACP requested %s, which this client does not implement — refusing so "
            "the agent falls back instead of blocking",
            method,
        )
        await self._acp_respond(
            req_id,
            error={"code": -32601, "message": f"client does not implement {method}"},
        )

    async def _finalize_stranded_turn(self, reason: str, detail: str = "") -> None:
        """Emit a terminal result so a turn can never be left open.

        THE INVARIANT: a prompt that stops being awaited MUST produce a result. The
        broker closes a turn only on a `result` event, so any path that abandons the
        wait without emitting one strands the session — it shows as permanently
        working and every later message queues behind a turn that can never finish.
        `lexi-frontend-voice` sat like that for 2.5 h, then again for 11 h.

        Timeout was fixed first; this covers the rest — transport teardown mid-turn
        (CancelledError, which is a BaseException and so slipped past `except
        Exception` entirely) and any unexpected failure with no steer queued.
        """
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
        result = self._make_result_from_acp(
            {"stopReason": reason, "sessionId": self._session_id, "error": message}
        )
        self._last_result = result
        try:
            await self._emit(result)
        except Exception as exc:  # pragma: no cover - teardown races
            logger.debug("Grok ACP could not emit the %s result: %r", reason, exc)

    async def _acp_notify(self, method: str, params: dict) -> None:
        """Send a JSON-RPC NOTIFICATION (no id, no reply expected)."""
        if self._process is None or self._process.stdin is None:
            return
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self._process.stdin.write((json.dumps(payload) + "\n").encode())
        await self._process.stdin.drain()

    async def _acp_respond(
        self, req_id: Any, *, result: dict | None = None, error: dict | None = None
    ) -> None:
        """Write a JSON-RPC response for an agent-initiated request."""
        if self._process is None or self._process.stdin is None:
            return
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result or {}
        try:
            self._process.stdin.write((json.dumps(payload) + "\n").encode())
            await self._process.stdin.drain()
        except Exception as exc:  # pragma: no cover - pipe teardown races
            logger.warning("Grok ACP failed to answer request %s: %r", req_id, exc)

    async def _acp_initialize(self) -> None:
        # CLIENT CAPABILITIES ARE A PROMISE, NOT A WISH LIST.
        #
        # This used to advertise fs.readTextFile / fs.writeTextFile / terminal — none
        # of which the transport implements. In ACP those flags tell the agent "the
        # CLIENT will perform file I/O for you", so Grok stopped doing its own and
        # sent us `fs/read_text_file` REQUESTS instead. Nothing answered them, so the
        # agent blocked mid-tool: every file-touching turn hung until the 300 s prompt
        # timeout, wrote nothing, and surfaced as "Grok just stops".
        #
        # Measured on grok 1.0.4, same prompt, only this declaration changed:
        #   fs caps true  -> completed=False wrote_file=False inbound={fs/read_text_file}
        #   fs caps false -> completed=True  wrote_file=True  inbound={}
        #
        # Grok runs as a local subprocess rooted in the workspace and is perfectly able
        # to read and write for itself, so declaring false is both honest and better.
        # `_handle_agent_request` is the backstop if a future version asks anyway.
        result = await self._acp_send(
            "initialize",
            {
                # Integer, matching the agent's own `"protocolVersion": 1` in its reply.
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
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
        self._read_effort_options(result)
        if self._reasoning_effort:
            await self.send_control("set_effort", effort=self._reasoning_effort)
        logger.info("Grok ACP new session established: %s", self._session_id)

    async def send_message(
        self, content: str, *, msg_id: str | None = None, request_id: str | None = None
    ) -> None:
        """Send a user turn and drain any steers queued while it ran.

        ACP turns are sequential, so a mid-turn steer (see ``send_control``)
        interrupts the in-flight prompt and queues its text; this loop then
        resumes within the SAME locked turn so the follow-up is sent
        immediately instead of stalling behind the per-turn lock.
        """
        async with self._lock:
            next_content: str | None = content
            while next_content is not None:
                await self._issue_prompt(next_content)
                next_content = self._pending_steers.pop(0) if self._pending_steers else None

    async def _issue_prompt(self, content: str) -> None:
        """Issue one ACP session/prompt and wait (bounded) for it to resolve.

        Caller holds ``self._lock``. The reader streams mapped chunks and emits
        the final result; we wait only to preserve turn ordering. An interrupt
        (e.g. a steer) resolves the future early — we return so send_message can
        issue the queued follow-up.
        """
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

        try:
            self._process.stdin.write((json.dumps(msg) + "\n").encode())
            await self._process.stdin.drain()
            if self._early_results.pop(req_id, None) is None:
                await asyncio.wait_for(asyncio.shield(fut), timeout=self._prompt_timeout)
        except TimeoutError:
            # A TIMEOUT MUST STILL END THE TURN.
            #
            # This used to log and return, emitting nothing. The broker only closes a
            # turn when a `result` event arrives, so the turn stayed `in_progress`
            # FOREVER: the UI showed a permanently working session and every later
            # message queued behind a turn that could never finish. Seen live on
            # `lexi-frontend-voice` (2026-08-16) — Grok fired six parallel Read/Grep
            # calls at 19:03:28, none ever returned, and the session was still wedged
            # two and a half hours later with the user's next messages stuck behind it.
            #
            # Whatever the agent did, the client's contract is the same: end the turn,
            # say why, and hand control back. Anything else strands the session.
            logger.warning(
                "Grok ACP prompt did not complete within timeout (%.1fs) — cancelling the "
                "turn and finalizing so the session does not wedge",
                self._prompt_timeout,
            )
            # Best-effort: tell the agent to abandon the turn, so it is not left running
            # tools against a client that has stopped waiting.
            try:
                await self._acp_notify("session/cancel", {"sessionId": self._session_id})
            except Exception as exc:  # pragma: no cover - notify is advisory
                logger.debug("Grok ACP cancel after timeout failed: %r", exc)
            timeout_result = self._make_result_from_acp(
                {
                    "stopReason": "timeout",
                    "sessionId": self._session_id,
                    "error": (
                        f"The model stopped responding for {self._prompt_timeout:.0f}s and the "
                        "turn was cancelled. Any tool calls still shown without a result never "
                        "completed. Send another message to continue."
                    ),
                }
            )
            self._last_result = timeout_result
            await self._emit(timeout_result)
        except asyncio.CancelledError:
            # The transport was torn down mid-turn (session stop/restart, broker
            # shutdown). CancelledError is a BaseException, so it never reached the
            # handler below — the coroutine simply vanished and the turn was left
            # open forever, exactly like the timeout case. Close it before re-raising.
            await self._finalize_stranded_turn("cancelled")
            raise
        except Exception as exc:
            if self._pending_steers:
                # Interrupted BY A STEER — legitimate. The reader already streamed
                # what it had and the turn loop issues the queued follow-up, so the
                # turn continues and must NOT be closed here.
                logger.info("Grok ACP prompt ended early (interrupted by steer)")
            else:
                # Ended early with nothing queued: the turn has no other way to end.
                logger.warning("Grok ACP prompt failed (%r) — finalizing the turn", exc)
                await self._finalize_stranded_turn("error", detail=repr(exc))
        finally:
            self._pending.pop(req_id, None)
            self._current_prompt_id = None
            if not fut.done():
                fut.cancel()

    # ------------------------------------------------------------------
    # Event mapping (ACP -> broker-expected Claude-style events)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Event envelopes — Claude/Codex wire parity
    # ------------------------------------------------------------------
    #
    # THE ENVELOPE IS NOT DECORATION. The broker's LIVE turn assembly reads
    # `data["message"]["content"]` with no fallback (broker `_handle_cli_event`,
    # the `event_type == "assistant"` branch), because that is the Anthropic SDK
    # shape Claude and Codex both emit. This transport emitted a BARE
    # `{"type": "assistant", "content": [...]}`, so the live path read an empty
    # block list and every Grok tool call was dropped from the saved turn: a real
    # session stored its reasoning and its answer, and not one tool row.
    #
    # It hid well. The REBUILD path (`transcript_reducer._content_blocks`) does
    # accept both shapes, so a rebuild-based test reduces the bare form correctly —
    # while the live path, which is what actually builds and persists the turn,
    # silently dropped it.
    #
    # (The asymmetry itself is worth removing broker-side so no future transport
    # can trip on it; that is a shared-code change and deliberately not made here,
    # where the brief is Grok only.)

    def _assistant_event(self, blocks: list[dict]) -> dict:
        """An assistant frame in the shape the live broker path actually reads."""
        return {
            "type": "assistant",
            "message": {"model": self._model, "content": blocks},
            # Bare `content` kept alongside for readers that take the flat shape
            # (the broker's artifact tracker does; belt and braces, costs nothing).
            "content": blocks,
        }

    def _user_event(self, blocks: list[dict]) -> dict:
        """A user frame — the role a tool_result must ride in (see the tool_call_update branch)."""
        return {"type": "user", "message": {"content": blocks}, "content": blocks}

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

        # Tool call started.
        #
        # HIERARCHICAL PARITY (the reason this carries an id at all). The block used to
        # be emitted WITHOUT ``id``, so nothing downstream could pair a call with its
        # result: the transcript reducer had no tool_use_id to match, hierarchical mode
        # could not attach output to a call, and per-tool timing had no start to close.
        # ACP hands us ``toolCallId`` — it is now the block id, exactly as Codex uses
        # its item_id.
        if su in ("tool_call", "toolCall", "tool_use"):
            meta = _grok_tool_meta(update)
            tool_name = (
                meta.get("name")
                or update.get("tool")
                or update.get("name")
                or update.get("title")
                or "tool"
            )
            args = (
                update.get("rawInput")
                or update.get("arguments")
                or update.get("input")
                or update.get("args")
                or {}
            )
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"raw": args}
            normalized = _map_grok_tool(tool_name, meta.get("label"))
            call_id = update.get("toolCallId") or update.get("tool_call_id") or ""
            if call_id:
                # Remember the call so its update can be emitted as a paired tool_result
                # under the SAME name, and its duration stamped on completion.
                self._open_tool_calls[call_id] = {
                    "name": normalized,
                    "started_at": _utc_now_iso(),
                }
            block: dict = {"type": "tool_use", "name": normalized, "input": args}
            if call_id:
                block["id"] = call_id
            return self._assistant_event([block])

        # Tool progress / completion.
        #
        # A terminal status becomes a real ``tool_result`` paired by ``tool_use_id`` —
        # NOT another tool_use. Emitting a second tool_use per update was doubly wrong:
        # the output never attached to its call, and every UI tool tally counted the
        # same tool two-to-three times (a 4-tool turn reported 13).
        # Non-terminal updates are dropped: they are progress, and forwarding them was
        # the source of that inflation.
        if su in ("tool_call_update", "toolCallUpdate", "tool_result"):
            call_id = update.get("toolCallId") or update.get("tool_call_id") or ""
            status = str(update.get("status") or update.get("kind") or "").lower()
            if status and status not in _TERMINAL_TOOL_STATUSES:
                return None
            open_call = self._open_tool_calls.pop(call_id, None) if call_id else None
            payload = (
                update.get("rawOutput")
                if update.get("rawOutput") is not None
                else update.get("result")
                if update.get("result") is not None
                else update.get("output")
            )
            if payload is None:
                payload = update.get("content") or ""
            if not isinstance(payload, str):
                try:
                    payload = json.dumps(payload, ensure_ascii=False)
                except Exception:
                    payload = str(payload)
            if not call_id:
                # Un-correlatable: without an id this cannot become a tool_result, and a
                # bare tool_use would re-introduce the double count. Drop it.
                return None
            block = {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": payload,
            }
            if status in _ERROR_TOOL_STATUSES:
                block["is_error"] = True
            # D1 per-tool timing — the stamp hierarchical rows read for "ran in 3m 12s".
            block[TOOL_ENDED_AT] = _utc_now_iso()
            if open_call and open_call.get("started_at"):
                block["started_at"] = open_call["started_at"]
            # ROLE IS LOAD-BEARING: a tool_result rides in a **user** frame, not an assistant
            # one. That is the Anthropic wire convention Claude and Codex both follow (the
            # model emits the call, the harness answers it), and the whole stack is built on
            # it: the transcript reducer only harvests tool_result blocks from `user` frames,
            # and the broker has a dedicated `_is_tool_result_only_user_event` so such a frame
            # does not become a visible user turn. Emitting these as `assistant` — as this
            # transport did — meant the reducer silently dropped every one, so a Grok session
            # stored its tool CALLS with no results: hierarchical mode showed the answer and
            # never the tool rows. Unit tests passed throughout, because they asserted the
            # block we built rather than the turn the reducer built from it.
            return self._user_event([block])

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
    def is_turn_active(self) -> bool:
        """True while an ACP prompt is in flight (drives the broker's steer route)."""
        return self._current_prompt_id is not None

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
            set_effort=True,
            # ACP turns are sequential (no native mid-turn input like tmux), so a
            # mid-turn message steers by interrupting the current turn and
            # resuming with the new text — the SDK's interrupt_resume model.
            steer=True,
            steering_mode="interrupt_resume",
            # set_model / rewind / mcp / permission_requests etc. are no-op or future ACP extensions
            skills=True,  # Grok Build surfaces skills and subagents as tools
        )

    # ------------------------------------------------------------------
    # Control support for broker parity (interrupt, etc.)
    # ------------------------------------------------------------------

    def _read_effort_options(self, result: dict) -> None:
        self._effort_options = next(
            (
                option
                for option in result.get("configOptions", [])
                if option.get("category") == "thought_level"
            ),
            {},
        )

    async def get_effort(self) -> dict:
        option = self._effort_options
        levels = [item["value"] for item in option.get("options", []) if "value" in item]
        return {
            "current": option.get("currentValue", ""),
            "levels": levels,
            "mutable": bool(levels),
            "applies_to": "next_model_call",
        }

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        """Handle server-initiated controls (interrupt, steer) for broker parity."""
        if subtype == "set_effort":
            effort = validate_effort(
                str(kwargs.get("effort") or ""), (await self.get_effort())["levels"]
            )
            result = await self._acp_send(
                "session/set_config_option",
                {
                    "sessionId": self._session_id,
                    "configId": self._effort_options["id"],
                    "value": effort,
                },
            )
            self._read_effort_options(result)
            if self._effort_options.get("currentValue") != effort:
                raise RuntimeError("Grok did not confirm the requested effort")
            self._reasoning_effort = effort
            return

        if subtype == "interrupt":
            logger.info("GrokACPTransport: received interrupt control")
            self._interrupt_current_prompt(reason="interrupted by control")
            return

        if subtype in {"redirect", "steer"}:
            content = kwargs.get("content")
            text = str(content) if content is not None else ""
            if not text:
                return
            if self._current_prompt_id is not None:
                # Mid-turn: queue the steer and interrupt the running prompt. The
                # in-flight send_message loop then resumes with this text — we do
                # NOT start a second send_message that would block on the lock.
                logger.info("GrokACPTransport: steering active turn")
                self._pending_steers.append(text)
                self._interrupt_current_prompt(reason="steered")
            else:
                # Idle: just start a fresh turn.
                await self.send_message(text)
            return

        # Other controls (set_model, rewind, mcp_set_servers, etc.) not directly supported
        # by basic ACP stdio in yolo mode; log for observability.
        logger.debug(
            "GrokACPTransport.send_control(%s, %s) — no-op "
            "(ACP stdio yolo; use always-approve path)",
            subtype,
            kwargs,
        )

    def _interrupt_current_prompt(self, *, reason: str) -> None:
        """SIGINT the CLI and resolve the in-flight prompt future so the turn ends.

        Clearing here is idempotent with ``_issue_prompt``'s finally (pop with a
        default, then None), so either path can run first.
        """
        if self._process and self._process.returncode is None:
            try:
                self._process.send_signal(signal.SIGINT)
                logger.info("GrokACPTransport: sent SIGINT to interrupt current turn")
            except Exception as exc:
                logger.warning("GrokACPTransport: SIGINT failed: %r", exc)
        if self._current_prompt_id is not None:
            fut = self._pending.pop(self._current_prompt_id, None)
            if fut and not fut.done():
                fut.set_exception(RuntimeError(reason))
            self._current_prompt_id = None

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
