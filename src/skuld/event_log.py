"""Event-log, tracing, usage, and activity reporting behavior for Skuld."""

import asyncio
import logging
import signal
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import WebSocket

from skuld.conversation_models import ConversationTurn
from skuld.session_artifacts import SessionArtifacts

FORGE_SESSIONS_PATH = "/api/v1/forge/sessions"
FORGE_CHRONICLES_PATH = "/api/v1/forge/chronicles"
FORGE_EVENTS_PATH = "/api/v1/forge/events"
FORGE_TRACE_SPANS_START_PATH = "/api/v1/forge/spans/start"
FORGE_TRACE_SPANS_COMPLETE_PATH = "/api/v1/forge/spans/complete"

logger = logging.getLogger("skuld.broker")


class EventLogRejectedError(RuntimeError):
    """Volundr permanently rejected the durable event log — retrying cannot help.

    Raised for client-side rejections (bad session id, bad credentials, wrong
    base URL). The durable log is configured, so per the no-fallbacks rule the
    broker must stop rather than run on while silently recording nothing.
    """


def sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\\n", "\\\\n").replace("\\r", "\\\\r")


_sanitize_log = sanitize_log


class EventLogMixin:
    """Broker behavior for durable events, tracing, pipeline, and usage."""

    def _build_auth_headers(self) -> dict[str, str]:
        """Build authentication headers for Volundr API calls.

        Priority:
        1. The configured external API token when explicitly injected for external use.
        2. Short-lived workload JWT exchanged from projected service-account token.
        3. User JWT from WebSocket connection (fallback for dev/local).
        4. Empty (dev mode — no auth, Volundr backend must accept).
        """
        service_token = self._settings.external_api_token
        if service_token:
            return {"Authorization": f"Bearer {service_token}"}

        if self._workload_jwt and self._workload_jwt_expires_at - 30 > time.time():
            return {"Authorization": f"Bearer {self._workload_jwt}"}

        if self._user_jwt:
            return {"Authorization": f"Bearer {self._user_jwt}"}

        logger.debug("No auth token available — requests will be unauthenticated")
        return {}

    async def _refresh_workload_token(self) -> None:
        """Refresh cached workload JWT from the projected service-account token."""
        now = time.time()
        if self._workload_jwt and self._workload_jwt_expires_at - 30 > now:
            return

        workload = self._settings.workload_identity
        token_path = Path(workload.token_file)
        if not token_path.exists():
            return

        proof = token_path.read_text(encoding="utf-8").strip()
        if not proof:
            return

        exchange_url = workload.exchange_url.strip()
        if not exchange_url:
            exchange_url = f"{self.volundr_api_url.rstrip('/')}/api/v1/tokens/workload/exchange"
        audiences = list(workload.audiences)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    exchange_url,
                    json={"token": proof, "audiences": audiences},
                )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.warning("Workload token exchange failed", exc_info=True)
            return

        token = str(payload.get("token") or "")
        if not token:
            logger.warning("Workload token exchange returned no token")
            return

        expires_at = payload.get("expiresAt") or payload.get("expires_at")
        self._workload_jwt = token
        self._workload_jwt_expires_at = float(expires_at or (now + 300))

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Lazy-init HTTP client for Volundr API calls.

        Recreates the client when the JWT changes so the Authorization
        header stays current.
        """
        await self._refresh_workload_token()
        headers = self._build_auth_headers()
        auth_header = headers.get("Authorization", "")
        if self._http_client is not None and (
            self._http_client_jwt != self._user_jwt or self._http_client_auth_header != auth_header
        ):
            await self._http_client.aclose()
            self._http_client = None

        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self.volundr_api_url,
                timeout=10.0,
                headers=headers,
            )
            self._http_client_jwt = self._user_jwt
            self._http_client_auth_header = auth_header
        return self._http_client

    def _next_sequence(self) -> int:
        """Return a monotonically increasing sequence number."""
        seq = self._event_sequence
        self._event_sequence += 1
        return seq

    @staticmethod
    def _trace_label(name: str, *, limit: int = 120) -> str:
        """Trim trace labels so the API payloads stay readable."""
        value = " ".join(str(name or "").split())
        if len(value) <= limit:
            return value
        return value[: limit - 3].rstrip() + "..."

    async def _start_trace_span(
        self,
        *,
        kind: str,
        name: str,
        source_service: str = "skuld",
        parent_span_id: uuid.UUID | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
        actor_label: str | None = None,
        attributes: dict[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> uuid.UUID | None:
        """Open a trace span in Volundr for this session."""
        if not self.volundr_api_url:
            return None
        client = await self._get_http_client()
        span_id = uuid.uuid4()
        payload = {
            "id": str(span_id),
            "session_id": str(self.session_id),
            "trace_id": str(self._trace_id),
            "kind": kind,
            "name": self._trace_label(name),
            "source_service": source_service,
            "started_at": (started_at or datetime.now(UTC)).isoformat(),
            "attributes": attributes or {},
        }
        if parent_span_id is not None:
            payload["parent_span_id"] = str(parent_span_id)
        if actor_type is not None:
            payload["actor_type"] = actor_type
        if actor_id is not None:
            payload["actor_id"] = actor_id
        if actor_label is not None:
            payload["actor_label"] = actor_label
        try:
            response = await client.post(FORGE_TRACE_SPANS_START_PATH, json=payload)
            if response.status_code < 300:
                return span_id
            logger.debug(
                "Trace span start failed (%d): %s",
                response.status_code,
                response.text[:200],
            )
        except Exception:
            logger.debug("Failed to start trace span", exc_info=True)
        return None

    async def _finish_trace_span(
        self,
        span_id: uuid.UUID | None,
        *,
        status: str = "completed",
        attributes: dict[str, Any] | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        """Finish an already-open trace span."""
        if span_id is None or not self.volundr_api_url:
            return
        client = await self._get_http_client()
        payload = {
            "session_id": str(self.session_id),
            "ended_at": (ended_at or datetime.now(UTC)).isoformat(),
            "status": status,
            "attributes": attributes or {},
        }
        try:
            response = await client.post(
                f"/api/v1/forge/spans/{span_id}/finish",
                json=payload,
            )
            if response.status_code < 300:
                return
            logger.debug(
                "Trace span finish failed (%d): %s",
                response.status_code,
                response.text[:200],
            )
        except Exception:
            logger.debug("Failed to finish trace span id=%s", span_id, exc_info=True)

    async def _complete_trace_span(
        self,
        *,
        kind: str,
        name: str,
        source_service: str = "skuld",
        parent_span_id: uuid.UUID | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
        actor_label: str | None = None,
        attributes: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        duration_ms: int | None = None,
        status: str = "completed",
    ) -> uuid.UUID | None:
        """Record a completed span in a single request."""
        if not self.volundr_api_url:
            return None
        client = await self._get_http_client()
        span_id = uuid.uuid4()
        actual_started_at = started_at or datetime.now(UTC)
        payload = {
            "id": str(span_id),
            "session_id": str(self.session_id),
            "trace_id": str(self._trace_id),
            "kind": kind,
            "name": self._trace_label(name),
            "source_service": source_service,
            "started_at": actual_started_at.isoformat(),
            "status": status,
            "attributes": attributes or {},
        }
        if ended_at is not None:
            payload["ended_at"] = ended_at.isoformat()
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if parent_span_id is not None:
            payload["parent_span_id"] = str(parent_span_id)
        if actor_type is not None:
            payload["actor_type"] = actor_type
        if actor_id is not None:
            payload["actor_id"] = actor_id
        if actor_label is not None:
            payload["actor_label"] = actor_label
        try:
            response = await client.post(FORGE_TRACE_SPANS_COMPLETE_PATH, json=payload)
            if response.status_code < 300:
                return span_id
            logger.debug(
                "Trace span complete failed (%d): %s",
                response.status_code,
                response.text[:200],
            )
        except Exception:
            logger.debug(
                "Failed to complete trace span kind=%s name=%s",
                _sanitize_log(kind),
                _sanitize_log(name),
                exc_info=True,
            )
        return None

    async def _ensure_session_trace_started(self) -> None:
        """Ensure the root session lifecycle span is open."""
        if self._trace_session_span_id is not None:
            return
        self._trace_session_span_id = await self._start_trace_span(
            kind="session.lifecycle",
            name=self._settings.session.name or "session",
            source_service="skuld",
            actor_type="system",
            actor_id=self.session_id,
            actor_label=self._settings.session.name or "session",
            attributes={
                "model": self.model,
                "workspace_path": self.workspace_dir,
                "workflow_enabled": bool(self._has_workflow_trigger()),
            },
        )

    # -- Durable event log (full-fidelity transcript capture) -----------------

    FORGE_LOG_PATH_TEMPLATE = "/api/v1/forge/sessions/{sid}/log"

    @staticmethod
    def _extract_request_id(data: dict) -> str | None:
        """Best-effort turn correlation id from a raw CLI frame."""
        rid = data.get("request_id")
        if isinstance(rid, str) and rid:
            return rid
        msg = data.get("message")
        if isinstance(msg, dict):
            inner = msg.get("request_id")
            if isinstance(inner, str) and inner:
                return inner
        return None

    def _enqueue_event_log(self, data: dict) -> None:
        """Buffer a raw CLI frame for durable persistence. Never raises.

        Runs for every frame regardless of attached channels — this is what
        guarantees no agent output is dropped when no client is connected.
        """
        if not self._settings.event_log_enabled or not self.volundr_api_url:
            return
        self._event_log_seq += 1
        entry = {
            "seq": self._event_log_seq,
            "kind": str(data.get("type", "unknown"))[:64],
            "payload": data,
            "request_id": self._extract_request_id(data),
            # Emission time, captured HERE — without it the ingest stamps
            # arrival time, which skews replayed timelines whenever the POST
            # batch lags (rate-limit stalls, backend hiccups). Clients replay
            # these ts so an old session shows when things actually happened.
            "ts": datetime.now(UTC).isoformat(),
        }
        role = data.get("role")
        if isinstance(role, str):
            entry["role"] = role[:32]
        self._event_log_buffer.append(entry)
        # Safety valve: cap memory if the backend is unreachable for a long time.
        # Dropping the oldest is the least-bad option vs OOM-killing the broker,
        # and is logged loudly so the loss is visible.
        overflow = len(self._event_log_buffer) - self._settings.event_log_max_buffer
        if overflow <= 0:
            return
        dropped = self._event_log_buffer[:overflow]
        # True hole size: a prior overflow's sentinel may itself be among the frames
        # dropped this round. Counting it as a single frame would UNDER-report under
        # sustained overflow (the hole it stood for vanishes). Fold its accumulated
        # ``dropped`` count back in, and carry the earliest covered seq forward, so the
        # surviving sentinel always reports the TRUE size/range of the cumulative gap.
        true_dropped = 0
        first_dropped_seq = dropped[0].get("first_seq", dropped[0]["seq"])
        last_dropped_seq = dropped[-1].get("last_seq", dropped[-1]["seq"])
        for entry in dropped:
            payload = entry.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "log_gap":
                true_dropped += int(payload.get("dropped", 0))
                first_dropped_seq = min(first_dropped_seq, payload.get("first_seq", entry["seq"]))
                last_dropped_seq = max(last_dropped_seq, payload.get("last_seq", entry["seq"]))
                continue
            true_dropped += 1
            first_dropped_seq = min(first_dropped_seq, entry["seq"])
            last_dropped_seq = max(last_dropped_seq, entry["seq"])
        del self._event_log_buffer[:overflow]
        logger.warning(
            "event log buffer overflow — dropped %d oldest frames (backend unreachable?)",
            true_dropped,
        )
        # INV-2 durability floor: never leave a SILENT hole. Replace the dropped
        # frames with a single queryable sentinel so any reader can DETECT the
        # gap (and its size/range) instead of inferring "not yet flushed". The
        # sentinel rides at the front so it survives the very next flush.
        self._event_log_buffer.insert(
            0,
            {
                "seq": first_dropped_seq,
                "kind": "log_gap",
                "payload": {
                    "type": "log_gap",
                    "dropped": true_dropped,
                    "first_seq": first_dropped_seq,
                    "last_seq": last_dropped_seq,
                    "reason": "buffer_overflow",
                },
                "request_id": None,
                "ts": datetime.now(UTC).isoformat(),
            },
        )

    async def _emit_broker_frame(self, frame: dict) -> None:
        """Single choke point for BROKER-ORIGINATED frames (FR-1 / INV-1).

        Transport frames already funnel through ``_handle_cli_event`` (log first,
        then broadcast). Broker-synthesized frames — errors, permission_*,
        available_commands, the intermediary user_* events, etc. — must obey the
        SAME superset invariant: every frame that reaches a live client is in the
        durable log FIRST. Enqueue, then broadcast. The broadcast itself preserves
        existing behavior (best-effort; failures are swallowed by the caller's
        contract here so a dead socket never tears down broker logic).

        Do NOT route ``_handle_cli_event`` transport frames through this — they are
        already logged there and would be double-counted.
        """
        self._enqueue_event_log(frame)
        await self._channels.broadcast(frame)

    async def _send_broker_frame_to(self, sender_ws: Any, frame: dict) -> None:
        """Log a broker-originated frame, then send it to ONE channel (FR-1).

        Per-channel sends (e.g. an error echoed only to the sender) are still
        live frames a client sees, so they too must be persisted to the durable
        log first. Preserves the raw ``send_json`` semantics of the call site
        (exceptions propagate exactly as before).
        """
        self._enqueue_event_log(frame)
        await sender_ws.send_json(frame)

    async def _safe_send_broker_frame_to(self, websocket: WebSocket, frame: dict) -> bool:
        """Log a broker-originated frame, then safe-send it to ONE browser (FR-1).

        The first-connect path uses ``_safe_browser_send_json`` so a client that
        disconnected mid-handshake is handled gracefully (returns ``False`` to let
        the caller bail out) instead of raising. Frames sent this way — the
        ``system`` welcome and the ``capabilities`` catalog — are first-class
        broker-originated frames a live client sees (not re-sends of logged state),
        so they too must hit the durable log FIRST (INV-1). Enqueue, then safe-send;
        the bool gate semantics of ``_safe_browser_send_json`` are preserved.
        """
        self._enqueue_event_log(frame)
        return await self._safe_browser_send_json(websocket, frame)

    def _enqueue_human_turn_event(self, content: str, turn_id: str) -> None:
        """Persist a HUMAN message to the durable event log as a user frame.

        The CLI never echoes the operator's own prompt as a text frame — only
        tool_results arrive with role=user — so a transcript replayed purely
        from the durable log (web Code tab, iOS) omitted every human turn ("the
        transcript doesn't show my message"). Synthesize a string-content user
        frame, which the replay reducers render directly as a user turn (and
        string content distinguishes it from the CLI's block-list tool_result
        user frames).
        """
        if not content:
            return
        self._enqueue_event_log(
            {
                "type": "user",
                "role": "user",
                "uuid": turn_id,
                "message": {"role": "user", "content": content},
            }
        )

    async def _surface_remote_control_url(self, url: str) -> None:
        """Surface a remote-control pairing URL as an assistant turn everywhere.

        Appends it to conversation history (the conversation endpoint), enqueues a
        renderable assistant frame to the durable log (log-only replay), and
        broadcasts to any live channels — so whichever surface a client uses, the
        hand-off link to the native app is visible.
        """
        notice = (
            "🔗 **Remote control ready.** Drive this session from the Claude app "
            f"or claude.ai/code:\n\n{url}\n\n"
            "Open the link (or scan the QR in the host terminal) to attach the "
            "native app. This session is controlled remotely — messages typed "
            "here are not sent to the agent."
        )
        turn_id = str(uuid.uuid4())
        self._append_turn(ConversationTurn(id=turn_id, role="assistant", content=notice))
        frame = {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": notice}]},
        }
        self._enqueue_event_log(frame)
        try:
            await self._channels.broadcast(frame)
        except Exception:
            logger.debug("remote-control URL broadcast failed", exc_info=True)
        safe_url_for_log = url.replace("\r", "").replace("\n", "")
        logger.info(
            "Remote control pairing URL surfaced for session %s: %s",
            self.session_id,
            safe_url_for_log,
        )

    async def _event_log_flush_loop(self) -> None:
        """Background worker: drain the event-log buffer to Volundr with retry.

        Transient failures retry forever; a permanent rejection propagates so
        the worker's done-callback can take the broker down — a broker that
        keeps chatting while its configured transcript silently records
        nothing is the failure mode this codebase forbids.
        """
        interval = self._settings.event_log_flush_interval_ms / 1000.0
        while not self._event_log_stopping:
            await asyncio.sleep(interval)
            try:
                await self._flush_event_log()
            except EventLogRejectedError:
                raise
            except Exception:
                logger.debug("event log flush iteration failed", exc_info=True)

    @staticmethod
    def _is_transient_status(status: int) -> bool:
        """5xx, timeouts, and rate limits recover on retry; other rejections never do."""
        return status >= 500 or status in (408, 429)

    async def _flush_event_log(self) -> None:
        """Send one batch from the front of the buffer. Removes only on success."""
        if not self.volundr_api_url:
            return
        async with self._event_log_lock:
            batch = self._event_log_buffer[: self._settings.event_log_batch_size]
        if not batch:
            return

        client = await self._get_http_client()
        path = self.FORGE_LOG_PATH_TEMPLATE.format(sid=self.session_id)
        try:
            response = await client.post(path, json={"entries": batch})
        except Exception:
            logger.debug("event log POST failed — will retry", exc_info=True)
            return
        if response.status_code >= 300:
            if self._is_transient_status(response.status_code):
                logger.debug(
                    "event log POST rejected (%d): %s — will retry",
                    response.status_code,
                    response.text[:200],
                )
                return
            raise EventLogRejectedError(
                f"Volundr rejected the durable event log for session {self.session_id} "
                f"({response.status_code}): {response.text[:200]}. Retrying cannot fix "
                "this — session.id must be the Volundr session UUID and the broker's "
                "credentials must be valid. Fix the config, or unset volundr_api_url / "
                "set event_log_enabled: false to run without a durable log."
            )
        # Idempotent on (session_id, seq), so removing exactly the sent count is
        # safe even if newer frames were appended during the POST.
        async with self._event_log_lock:
            del self._event_log_buffer[: len(batch)]

    async def _init_event_log(self) -> None:
        """Resume the seq counter from the backend so restarts don't collide.

        The PK is (session_id, seq); if a restarted broker reset seq to 0 its
        appends would hit ON CONFLICT DO NOTHING and silently vanish. Seeding
        from the stored head keeps the sequence monotonic across restarts.

        Runs during broker startup, so a head fetch that cannot succeed fails
        the boot: guessing seq 0 instead risks exactly the silent swallow the
        resume exists to prevent, and a permanent rejection here means every
        later flush would fail too.
        """
        if not self._settings.event_log_enabled or not self.volundr_api_url:
            return
        client = await self._get_http_client()
        path = self.FORGE_LOG_PATH_TEMPLATE.format(sid=self.session_id) + "/head"
        try:
            response = await client.get(path)
        except httpx.HTTPError as exc:
            raise EventLogRejectedError(
                f"Volundr is unreachable for the event-log head fetch at "
                f"{self.volundr_api_url}{path}: {exc}. The durable event log is "
                "configured (volundr_api_url is set) so it must work — fix "
                "connectivity, or unset volundr_api_url / set event_log_enabled: "
                "false to run without a durable log."
            ) from exc
        if response.status_code >= 300:
            raise EventLogRejectedError(
                f"Volundr rejected the event-log head fetch for session "
                f"{self.session_id} ({response.status_code}): {response.text[:200]}. "
                "session.id must be the Volundr session UUID and the broker's "
                "credentials must be valid. Fix the config, or unset "
                "volundr_api_url / set event_log_enabled: false to run without a "
                "durable log."
            )
        head = int(response.json().get("latest_seq", 0))
        await self._resume_seq_from_head(head)
        self._event_log_task = asyncio.create_task(self._event_log_flush_loop())
        self._event_log_task.add_done_callback(self._on_event_log_worker_done)
        logger.info("Durable event log started (resume seq=%d)", self._event_log_seq)

    @staticmethod
    def _on_event_log_worker_done(task: asyncio.Task) -> None:
        """Take the broker down when the event-log worker dies on a rejection.

        The worker runs detached, so an exception in it is otherwise invisible
        until shutdown — the broker would keep serving chat while its
        configured transcript records nothing. SIGTERM triggers uvicorn's
        graceful shutdown; the supervisor restart then fails loudly in
        ``_init_event_log`` until the config is fixed.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        logger.critical("Durable event log worker died: %s — terminating broker", exc)
        signal.raise_signal(signal.SIGTERM)

    async def _resume_seq_from_head(self, head: int) -> None:
        """Seed the seq counter from the backend head WITHOUT losing window frames.

        The head fetch in ``_init_event_log`` is an ``await``, so the event loop is
        free to run other tasks meanwhile — the *capture window*. Agent output can
        be enqueued during it, taking provisional seqs ``1..W``. Naively assigning
        ``_event_log_seq = head`` would leave those buffered frames carrying seqs
        that overlap the backend's already-stored ``1..head``; the PK is
        ``(session_id, seq)`` with ``ON CONFLICT DO NOTHING``, so they would be
        silently swallowed on flush (INV-8c collision).

        Re-base the whole run above the head: shift every already-buffered frame by
        ``head`` so the window frames (provisional seqs ``1..W``) become
        ``head+1 .. head+W`` (preserving their relative order), and continue the
        counter from ``head+W``. If nothing was captured during the window this
        collapses to the plain ``_event_log_seq = head`` resume.
        """
        async with self._event_log_lock:
            if head <= 0:
                # Fresh start (no prior durable state) — provisional seqs are
                # already correct; nothing to re-base.
                return
            for entry in self._event_log_buffer:
                entry["seq"] += head
            self._event_log_seq += head

    async def _stop_event_log(self) -> None:
        """Drain remaining frames and stop the worker on shutdown."""
        self._event_log_stopping = True
        if self._event_log_task is not None:
            self._event_log_task.cancel()
            await asyncio.gather(self._event_log_task, return_exceptions=True)
            self._event_log_task = None
        # Final best-effort drain so the last turn isn't lost on shutdown.
        for _ in range(self._settings.event_log_max_buffer):
            async with self._event_log_lock:
                remaining = len(self._event_log_buffer)
            if remaining == 0:
                return
            before = remaining
            try:
                await self._flush_event_log()
            except EventLogRejectedError:
                # Already shutting down — report the loss instead of aborting
                # the remaining teardown steps.
                logger.critical(
                    "Durable event log rejected during shutdown — %d frames lost",
                    remaining,
                    exc_info=True,
                )
                return
            async with self._event_log_lock:
                if len(self._event_log_buffer) >= before:
                    return  # made no progress (backend down) — give up

    async def _emit_pipeline_event(
        self,
        event_type: str,
        data: dict,
        *,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost: float | None = None,
        duration_ms: int | None = None,
        model: str | None = None,
    ) -> None:
        """Emit a raw event to the Volundr event pipeline.

        Fires as a background task — must not raise or block the WebSocket.
        """
        if not self.volundr_api_url:
            return

        client = await self._get_http_client()
        from datetime import datetime

        payload = {
            "session_id": self.session_id,
            "event_type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": data,
            "sequence": self._next_sequence(),
        }

        if tokens_in is not None:
            payload["tokens_in"] = tokens_in
        if tokens_out is not None:
            payload["tokens_out"] = tokens_out
        if cost is not None:
            payload["cost"] = cost
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if model is not None:
            payload["model"] = model

        try:
            response = await client.post(FORGE_EVENTS_PATH, json=payload)
            if response.status_code < 300:
                logger.debug("Pipeline event emitted: %s", event_type)
            else:
                logger.debug(
                    "Pipeline event failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )
        except Exception:
            logger.debug("Failed to emit pipeline event: %s", event_type, exc_info=True)

    async def _report_usage(self, result_data: dict) -> None:
        """Report token usage from a CLI result event to the Volundr API.

        Fires as a background task — must not raise or block the WebSocket.
        """
        if not self.volundr_api_url:
            return

        model_usage = result_data.get("modelUsage", {})
        if not model_usage:
            logger.debug("No modelUsage in result event, skipping usage report")
            return

        client = await self._get_http_client()
        url = self._settings.usage_report_path or (f"{FORGE_SESSIONS_PATH}/{self.session_id}/usage")

        for model_id, usage in model_usage.items():
            tokens = (
                usage.get("inputTokens", 0)
                + usage.get("outputTokens", 0)
                + usage.get("cacheReadInputTokens", 0)
                + usage.get("cacheCreationInputTokens", 0)
            )
            if tokens <= 0:
                continue

            cost = usage.get("costUSD")
            payload = {
                "tokens": tokens,
                "provider": "cloud",
                "model": model_id,
                "message_count": 1,
            }
            if cost is not None:
                payload["cost"] = cost

            try:
                response = await client.post(url, json=payload)
                if response.status_code < 300:
                    logger.info("Reported usage")
                else:
                    logger.warning(
                        "Usage report failed (%d): %s",
                        response.status_code,
                        response.text[:200],
                    )
            except Exception:
                logger.warning("Failed to report usage", exc_info=True)

    async def _report_timeline_event(self, event: dict) -> None:
        """Report a single timeline event to the Volundr API.

        Fires as a background task — must not raise or block the WebSocket.
        The event dict must contain at minimum: t, type, label.
        """
        if not self.volundr_api_url:
            return

        client = await self._get_http_client()
        url = f"{FORGE_CHRONICLES_PATH}/{self.session_id}/timeline"

        try:
            response = await client.post(url, json=event)
            if response.status_code < 300:
                logger.debug("Timeline event reported")
            else:
                logger.debug(
                    "Timeline event report failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )
        except Exception:
            logger.debug("Failed to report timeline event", exc_info=True)

    @staticmethod
    def _classify_pipeline_event(tool_ev: dict) -> str:
        """Map a timeline tool event dict to a SessionEventType value."""
        ev_type = tool_ev.get("type", "")
        action = tool_ev.get("action", "")
        if ev_type == "file":
            if action == "created":
                return "file_created"
            if action == "deleted":
                return "file_deleted"
            return "file_modified"
        if ev_type == "git":
            return "git_commit"
        if ev_type == "git_push":
            return "git_push"
        if ev_type == "terminal":
            return "terminal_command"
        return "tool_use"

    @staticmethod
    def _event_content_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Return normalized content blocks from either top-level or message payload."""
        content = data.get("content", [])
        if isinstance(content, list) and content:
            return [block for block in content if isinstance(block, dict)]
        message = data.get("message")
        if isinstance(message, dict):
            nested = message.get("content", [])
            if isinstance(nested, list):
                return [block for block in nested if isinstance(block, dict)]
        return []

    @classmethod
    def _is_tool_result_only_user_event(cls, data: dict[str, Any]) -> bool:
        """Return True when a user event only carries internal tool_result blocks."""
        blocks = cls._event_content_blocks(data)
        return bool(blocks) and all(block.get("type") == "tool_result" for block in blocks)

    @staticmethod
    def _extract_tool_result_preview(block: dict[str, Any]) -> str:
        """Return a compact text preview for a tool_result payload."""
        content = block.get("content", "")
        if isinstance(content, str):
            return content[:200]
        if isinstance(content, list):
            text_parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("text")
            ]
            return " ".join(text_parts)[:200]
        return ""

    async def _start_assistant_tool_trace_spans(self, data: dict[str, Any]) -> None:
        """Open child tool spans for assistant tool_use blocks."""
        if self._trace_assistant_span_id is None:
            return

        assistant_label = (
            self._settings.mesh.persona
            if getattr(self._settings, "mesh", None) is not None
            else None
        ) or self._settings.session.name

        for block in self._event_content_blocks(data):
            if block.get("type") != "tool_use":
                continue

            tool_name = str(block.get("name") or "tool").strip() or "tool"
            tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
            tool_key = str(block.get("id") or uuid.uuid4())
            if tool_key in self._trace_assistant_tool_spans:
                continue

            span_id = await self._start_trace_span(
                kind="tool.call",
                name=tool_name,
                parent_span_id=self._trace_assistant_span_id,
                actor_type="assistant",
                actor_id=self._observer_peer_id() or self.session_id,
                actor_label=assistant_label,
                attributes={
                    "tool_name": tool_name,
                    "tool_use_id": str(block.get("id") or ""),
                    "tool_input": tool_input,
                },
            )
            if span_id is None:
                continue

            self._trace_assistant_tool_spans[tool_key] = span_id
            self._trace_assistant_tool_order.append(tool_key)

            command = str(tool_input.get("command") or "").strip()
            if command:
                self._assistant_pending_commands[tool_key] = command

    def _pop_assistant_tool_trace_span(
        self,
        tool_use_id: str,
    ) -> tuple[uuid.UUID | None, str]:
        """Pop a pending assistant tool span by id, falling back to FIFO order."""
        tool_key = (
            tool_use_id if tool_use_id and tool_use_id in self._trace_assistant_tool_spans else ""
        )
        if not tool_key and self._trace_assistant_tool_order:
            tool_key = self._trace_assistant_tool_order[0]
        if not tool_key:
            return None, ""

        span_id = self._trace_assistant_tool_spans.pop(tool_key, None)
        if tool_key in self._trace_assistant_tool_order:
            self._trace_assistant_tool_order.remove(tool_key)
        command = self._assistant_pending_commands.pop(tool_key, "")
        return span_id, command

    async def _finish_assistant_tool_trace_spans_from_user_event(
        self, data: dict[str, Any]
    ) -> None:
        """Close assistant child tool spans from tool_result-only user events."""
        for block in self._event_content_blocks(data):
            if block.get("type") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id") or "")
            span_id, command = self._pop_assistant_tool_trace_span(tool_use_id)
            await self._finish_trace_span(
                span_id,
                status="failed" if bool(block.get("is_error")) else "completed",
                attributes={
                    "tool_use_id": tool_use_id,
                    "command": command,
                    "exit_code": SessionArtifacts._extract_exit_code(block),
                    "is_error": bool(block.get("is_error")),
                    "result_preview": self._extract_tool_result_preview(block),
                },
            )

    async def _finish_pending_assistant_tool_trace_spans(
        self,
        *,
        status: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Close any assistant tool spans still open on turn/session termination."""
        while self._trace_assistant_tool_order:
            tool_key = self._trace_assistant_tool_order.pop(0)
            span_id = self._trace_assistant_tool_spans.pop(tool_key, None)
            command = self._assistant_pending_commands.pop(tool_key, "")
            extra_attributes = dict(attributes or {})
            if command:
                extra_attributes.setdefault("command", command)
            extra_attributes.setdefault("tool_use_id", tool_key)
            await self._finish_trace_span(
                span_id,
                status=status,
                attributes=extra_attributes,
            )
        self._trace_assistant_tool_spans.clear()
        self._assistant_pending_commands.clear()
