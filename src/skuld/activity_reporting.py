"""Session lifecycle activity reporting behavior for Skuld."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from skuld.event_log import FORGE_SESSIONS_PATH

logger = logging.getLogger("skuld.broker")


class ActivityReportingMixin:
    """Broker behavior for activity transitions and attention state."""

    _RUNNING_RAW_STATES = ("active", "tool_executing")
    _TURN_END_RAW_STATES = ("idle", "stopped", "error")

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

    async def _report_activity_state(
        self, state: str, *, extra_metadata: dict[str, Any] | None = None
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

        if not self.volundr_api_url:
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
            await self._report_activity_state(
                state,
                extra_metadata={**self._activity_extra, "heartbeat": True},
            )
