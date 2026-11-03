"""Session lifecycle activity reporting behavior for Skuld."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from niuu.domain.work_facts import HOOK_KIND, hook_name, is_work_row
from niuu.ports.cli import CLITransport
from skuld.event_log import FORGE_SESSIONS_PATH

logger = logging.getLogger("skuld.broker")

TURN_BOUNDARY_FLUSH_SOURCE = "turn_boundary_flush"


def is_turn_boundary_flush(data: dict) -> bool:
    """A synthetic prompt-consumed frame the transport emits AFTER a turn ended (F12a).

    It exists only to flip a silently absorbed steer's bubble; it is not a turn start.
    """
    metadata = data.get("metadata")
    return isinstance(metadata, dict) and metadata.get("source") == TURN_BOUNDARY_FLUSH_SOURCE


def is_idle_prompt_hook(data: dict) -> bool:
    """Claude's own "waiting at the prompt" Notification hook (``idle_prompt``)."""
    if data.get("type") != HOOK_KIND or hook_name(data) != "Notification":
        return False
    payload = data.get("payload")
    return isinstance(payload, dict) and payload.get("notification_type") == "idle_prompt"


class ActivityReportingMixin:
    """Broker behavior for activity transitions and attention state."""

    _RUNNING_RAW_STATES = ("active", "tool_executing")
    _TURN_END_RAW_STATES = ("idle", "stopped", "error")
    # Flock peer events that mean a peer is doing work right now.
    _PEER_WORK_EVENT_TYPES = frozenset({"task_started", "thought", "thinking", "tool_start"})

    async def _report_session_start(self) -> None:
        """Report the session start timeline event (once)."""
        if self._session_start_reported:
            return
        self._session_start_reported = True
        await self._report_timeline_event(
            {
                "t": 0,
                "type": "session",
                "label": "Session started",
            }
        )
        # Emit session_start to event pipeline
        await self._emit_pipeline_event(
            "session_start",
            {
                "model": self.model,
                "session_name": self._settings.session.name,
            },
            model=self.model,
        )

    async def _report_room_peer_working(self) -> None:
        """Report ``active`` while flock peers work in a room-only workflow session.

        A flock workflow session has no CLI transport of its own, so the CLI
        frames that drive activity everywhere else never arrive; peer progress
        is the only evidence of work. A human gate, an unanswered peer help
        request, or a reported terminal flock outcome owns the state instead.
        """
        if not self._is_room_only_workflow_session():
            return
        if self._pending_attention or self._activity_state in ("stopped", "error"):
            return
        if any(request.status == "pending" for request in self._pending_help_requests.values()):
            return
        if (
            self._flock_completion_reported
            or self._flock_failure_reported
            or self._pending_terminal_activity is not None
        ):
            return
        await self._report_activity_state("active")

    async def _report_room_flock_idle(self) -> None:
        """Report ``idle`` once no flock peer has a task in flight.

        Peer work is the only thing that makes a room-only workflow session
        ``active``, so when the last started task completes and nothing owns
        the state instead, the session is idle. Staying ``active`` kept the
        heartbeat re-asserting work that had stopped: two builder sessions sat
        "busy" for eleven hours after their last peer finished, and nothing
        downstream could tell them from sessions still working. The last peer
        outcome rides along so whoever closes the session can say where the
        workflow stopped.
        """
        if not self._is_room_only_workflow_session():
            return
        if any(self._peer_tasks_in_flight.values()):
            return
        if self._activity_state not in self._RUNNING_RAW_STATES:
            return
        if self._pending_attention:
            return
        if any(request.status == "pending" for request in self._pending_help_requests.values()):
            return
        if (
            self._flock_completion_reported
            or self._flock_failure_reported
            or self._pending_terminal_activity is not None
        ):
            return
        extra = {"flock_idle": dict(self._last_flock_outcome)} if self._last_flock_outcome else None
        await self._report_activity_state("idle", extra_metadata=extra)

    @classmethod
    def _coarse_bucket(cls, state: str) -> str:
        """Collapse raw active/tool states into the client-visible running bucket."""
        return "running" if state in cls._RUNNING_RAW_STATES else state

    def _set_activity_state(self, state: str, metadata: dict[str, Any] | None = None) -> None:
        """Set activity state while maintaining coarse-state and turn anchors.

        Raw active/tool state continues to change, but ``_activity_state_since``
        stays stable while both states render as one running bucket.
        ``_turn_started_at`` survives awaiting-input and clears on turn end.

        ``metadata`` is accepted for symmetry/forward-compat; it is not stored
        here (the rich per-state context lives in ``_activity_extra``, managed by
        ``_report_activity_state``).
        """
        new_bucket = self._coarse_bucket(state)
        old_bucket = self._coarse_bucket(self._activity_state)
        if new_bucket == "running" and self._turn_started_at is None:
            self._turn_started_at = time.time()
        elif state in self._TURN_END_RAW_STATES:
            self._turn_started_at = None
        self._activity_state = state
        if new_bucket != old_bucket:
            self._activity_state_since = time.time()

    @staticmethod
    def _state_since_iso(epoch_seconds: float) -> str:
        """Render an internal epoch ``_activity_state_since`` as ISO8601 UTC.

        Matches the wire convention used for Volundr datetimes (``.isoformat()``
        on a tz-aware UTC datetime) so clients parse it the same way.
        """
        return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat()

    def _open_controls(self, state: str) -> list[dict[str, str]] | None:
        """The open human gates as ``[{request_id, kind, opened_at}]`` (F12d).

        Served by Forge on the session so the Code tab runs the same derived-state
        algorithm as the control plane. ``None`` means unknown: awaiting input on a
        gate with no request id, which a reader must never take as "none open".
        """
        opened = getattr(self, "_control_opened_at", None)
        if opened is None:
            opened = self._control_opened_at = {}
        pending = dict(self._pending_attention)
        for request_id in list(opened):
            if request_id not in pending:
                opened.pop(request_id, None)
        if state == "awaiting_input" and not pending:
            return None
        now = datetime.now(UTC).isoformat()
        return [
            {
                "request_id": request_id,
                "kind": kind,
                "opened_at": opened.setdefault(request_id, now),
            }
            for request_id, kind in pending.items()
        ]

    async def _report_activity_state(
        self,
        state: str,
        *,
        extra_metadata: dict[str, Any] | None = None,
        force: bool = False,
    ) -> bool:
        """Report activity state change to Volundr.

        States: provisioning, active, idle, tool_executing, awaiting_input,
        stopped, error. Debounces rapid transitions — only reports when the state
        actually changes, unless extra_metadata is attached (used by the
        heartbeat and by attention reports so they always land). The POST body
        ALWAYS carries ``state`` and ``state_since`` so a transition (including
        ``idle``) is never reported without its entered-at timestamp.
        """
        if (
            state == self._activity_state
            and not extra_metadata
            and not force
            and not self._activity_report_pending
        ):
            return True

        # Remember the rich context of a "real" (non-heartbeat) report so the
        # heartbeat can re-send it unchanged. A plain report (no extra) clears it.
        if not (extra_metadata and extra_metadata.get("heartbeat")):
            self._activity_extra = {
                k: v for k, v in (extra_metadata or {}).items() if k != "heartbeat"
            }

        # Canonical setter: flips the in-memory state and stamps _since on change.
        self._set_activity_state(state, extra_metadata)
        now = time.monotonic()
        self._last_activity_report = now

        metadata = {
            "turn_count": self._artifacts.turn_count,
            "duration_seconds": self._artifacts.duration_seconds,
        }
        # Ride the CLI/agent conversation id upward so Volundr can persist it
        # and resume the conversation when the session is restarted. Works for
        # both Claude (session UUID) and Codex (thread id) — every
        # resume-capable transport implements .session_id.
        cli_session_id = self._transport.session_id if self._transport else None
        if cli_session_id:
            metadata["cli_session_id"] = cli_session_id
        if extra_metadata:
            metadata.update(extra_metadata)
        metadata["open_controls"] = self._open_controls(state)

        # Capture the state and its anchors together BEFORE any await. Another CLI
        # event can advance the broker while credentials or the HTTP request waits.
        payload = {
            "state": state,
            "state_since": self._state_since_iso(self._activity_state_since),
            "turn_started_at": (
                self._state_since_iso(self._turn_started_at)
                if self._turn_started_at is not None
                else None
            ),
            "metadata": metadata,
        }
        terminal_kind = self._terminal_activity_kind(metadata)
        pending = None
        if terminal_kind:
            pending = {"kind": terminal_kind, "payload": payload}
            self._pending_terminal_activity = pending

        if not self.volundr_api_url or not self._settings.activity_reporting_enabled:
            return False

        # Stays set until Forge acknowledges a report for the state the broker is
        # still in, so the heartbeat retries a failed transition (including idle)
        # with its original anchors instead of dropping it.
        self._activity_report_pending = True
        delivered = await self._post_activity_payload(payload)
        if delivered and pending is not None:
            self._acknowledge_terminal_activity(pending)
        if delivered and (state, payload["state_since"]) == (
            self._activity_state,
            self._state_since_iso(self._activity_state_since),
        ):
            self._activity_report_pending = False
        return delivered

    @staticmethod
    def _terminal_activity_kind(metadata: dict[str, Any]) -> str:
        if metadata.get("completion_source") == "ravn_flock":
            return "completion"
        if metadata.get("failure_source") == "ravn_flock":
            return "failure"
        return ""

    async def _post_activity_payload(self, payload: dict[str, Any]) -> bool:
        """Post one exact activity payload and report whether Forge acknowledged it."""

        try:
            async with self._activity_report_lock:
                client = await self._get_http_client()
                resp = await client.post(
                    f"{FORGE_SESSIONS_PATH}/{self.session_id}/activity",
                    json=payload,
                )
            logger.info(
                "Activity report: state=%s status=%d url=%s",
                payload["state"],
                resp.status_code,
                resp.url,
            )
            if resp.status_code >= 300:
                logger.warning(
                    "Forge rejected activity state %s with HTTP %d",
                    payload["state"],
                    resp.status_code,
                )
                return False
            return True
        except Exception:
            logger.warning(
                "Failed to report activity state %s",
                payload["state"],
                exc_info=True,
            )
            return False

    def _acknowledge_terminal_activity(self, pending: dict[str, Any]) -> None:
        """Clear only the exact terminal report acknowledged by Forge."""
        if self._pending_terminal_activity is not pending:
            return
        self._pending_terminal_activity = None
        if pending["kind"] == "completion":
            self._flock_completion_reported = True
        elif pending["kind"] == "failure":
            self._flock_failure_reported = True

    async def _retry_pending_terminal_activity(self) -> bool:
        pending = self._pending_terminal_activity
        if pending is None or not self.volundr_api_url:
            return False
        delivered = await self._post_activity_payload(pending["payload"])
        if delivered:
            self._acknowledge_terminal_activity(pending)
        return delivered

    async def _enter_attention(
        self,
        request_id: str,
        kind: str,
        *,
        prompt: str = "",
        options: list | None = None,
    ) -> None:
        """Mark the session blocked on the user and report awaiting_input.

        ``kind`` is one of ``question`` | ``confirmation`` | ``permission``.
        The metadata travels to Volundr, which (re-)emits the high-urgency
        needs-input event a notification / push fan-out reacts to.
        """
        if request_id:
            self._pending_attention[request_id] = kind
        extra: dict[str, Any] = {"kind": kind, "request_id": request_id}
        if prompt:
            extra["prompt"] = prompt
        if options is not None:
            extra["options"] = options
        await self._report_activity_state("awaiting_input", extra_metadata=extra)

    async def _exit_attention(self, request_id: str) -> None:
        """Clear one pending human gate; resume when nothing else is blocking.

        Reports ``active`` once the last gate clears so the session leaves
        awaiting_input immediately — the next CLI frame refines the state.
        """
        self._pending_attention.pop(request_id, None)
        if self._pending_attention:
            # Still waiting on another gate: refresh open_controls (F12d) so readers
            # see one fewer, without leaving awaiting_input.
            if self._activity_state == "awaiting_input":
                extra = dict(self._activity_extra)
                if extra.get("request_id") == request_id:
                    # The detail named the gate just answered: point it at one still open.
                    remaining_id, remaining_kind = next(iter(self._pending_attention.items()))
                    extra = {"kind": remaining_kind, "request_id": remaining_id}
                await self._report_activity_state(
                    "awaiting_input", extra_metadata=extra or None, force=True
                )
            return
        if self._activity_state == "awaiting_input":
            await self._report_activity_state("active")

    @staticmethod
    def _attention_prompt_from_questions(data: dict) -> str:
        """Best-effort human-readable prompt from an ask_user_question frame."""
        questions = data.get("questions") or []
        for q in questions:
            if isinstance(q, dict):
                text = q.get("question") or q.get("header") or q.get("prompt")
                if text:
                    return str(text)
        return "The agent is asking a question"

    @staticmethod
    def _attention_prompt_from_permission(request: dict) -> str:
        """Best-effort human-readable prompt from a permission control_request."""
        tool = request.get("tool_name") or request.get("tool") or "a tool"
        description = request.get("description") or request.get("command")
        if description:
            return f"Permission to run {tool}: {description}"
        return f"Permission to run {tool}"

    def _observe_activity_row(self, data: dict) -> None:
        """Track the facts the self-heal and the heartbeat guard need (G10, F12b/F12c).

        Every transport frame (terminal frames included) proves the transport is alive;
        only work rows (``niuu.domain.work_facts``) prove the turn did work. A
        ``UserPromptSubmit`` starts a prompt whose work is still unproven.
        """
        self._last_transport_row_at = time.monotonic()
        kind = str(data.get("type", ""))
        if kind == HOOK_KIND and hook_name(data) == "UserPromptSubmit":
            self._prompt_work_seen = False
            return
        if is_work_row(kind, data):
            self._prompt_work_seen = True

    def _transport_turn_active(self) -> bool:
        return getattr(self._transport, "is_turn_active", False) is True

    def _transport_tracks_turns(self) -> bool:
        """Whether the transport reports its own turn flag (not the port's default).

        A transport that inherits ``CLITransport.is_turn_active`` always says False,
        which is "unknown", not "no turn in flight".
        """
        transport = self._transport
        if transport is None:
            return False
        prop = getattr(type(transport), "is_turn_active", None)
        return prop is not None and prop is not CLITransport.is_turn_active

    async def _heal_on_idle_prompt(self) -> bool:
        """Claude says it is idle at its prompt: drop a stale busy state (F12b).

        Applies only while the broker still reports active/tool_executing with no human
        gate pending, and either the transport has no turn in flight, or the current
        prompt never produced work (a dead ``UserPromptSubmit``).
        """
        if self._activity_state not in self._RUNNING_RAW_STATES or self._pending_attention:
            return False
        if self._transport_turn_active() and self._prompt_work_seen:
            return False
        await self._report_activity_state("idle", extra_metadata={"healed": "idle_prompt"})
        return True

    async def _heartbeat_guard(self, state: str) -> bool:
        """Decide whether a busy heartbeat may re-report ``state`` (F12c).

        A busy state is re-reported only while the transport produced a row within
        ``stale_output_seconds``. Past that, reconcile with the transport's own turn
        flag: no turn in flight → report idle; a turn in flight, or a transport that
        does not track turns → stay silent, so ``last_active`` stops advancing on a
        claim nothing supports (never a guessed idle).
        """
        if state not in self._RUNNING_RAW_STATES:
            return True
        stale_after = self._settings.activity_heartbeat.stale_output_seconds
        last_row = getattr(self, "_last_transport_row_at", None)
        if last_row is not None and time.monotonic() - last_row <= stale_after:
            return True
        if self._transport_tracks_turns() and not self._transport_turn_active():
            await self._report_activity_state("idle", extra_metadata={"healed": "heartbeat"})
        return False

    async def _activity_heartbeat_loop(self) -> None:
        """Re-report the current activity state on an interval while busy/blocked.

        Keeps the UI's "progressing" signal live during long turns and keeps
        ``last_active`` fresh so Volundr's liveness reaper does not stop a
        genuinely-busy or input-blocked session. Idle sessions are not
        heartbeated (an idle session that goes stale is exactly what the reaper
        is meant to catch). A failed transition, including idle, is retried
        until acknowledged; retries preserve its original state/turn anchors.
        """
        interval = self._settings.activity_heartbeat.interval_seconds
        while True:
            await asyncio.sleep(interval)
            if self._pending_terminal_activity is not None:
                await self._retry_pending_terminal_activity()
                continue
            state = self._activity_state
            if (
                state not in ("active", "tool_executing", "awaiting_input")
                and not self._activity_report_pending
            ):
                continue
            if not await self._heartbeat_guard(state):
                continue
            await self._report_activity_state(
                state,
                extra_metadata={**self._activity_extra, "heartbeat": True},
            )
