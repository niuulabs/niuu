"""Transport construction and broker process lifecycle."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import uuid
from pathlib import Path

from niuu.ports.cli import CLITransport
from niuu.utils import import_class, resolve_secret_kwargs
from skuld.broker_api import _rebuild_presented_registry
from skuld.chronicle_watcher import ChronicleWatcher
from skuld.codex_auth import CodexAuthProviderPort
from skuld.conversation_models import ConversationTurn
from skuld.service_manager import ServiceManager
from skuld.session_artifacts import _capture_git_workspace_checkpoint

logger = logging.getLogger("skuld.broker")


class TransportLifecycleMixin:
    """Own transport construction and broker startup/shutdown sequencing."""

    def _build_transport_kwargs(self) -> dict:
        """Return superset of kwargs that any transport constructor might need."""
        return {
            "workspace_dir": self.workspace_dir,
            "model": self.model,
            "reasoning_effort": self._settings.session.reasoning_effort,
            "sdk_port": self._settings.port,
            "session_id": self.session_id,
            "skip_permissions": self._settings.skip_permissions,
            "approval_policy": self._settings.approval_policy,
            "sandbox": self._settings.sandbox,
            "cli_binary": self._settings.cli_binary,
            "session_name": self._settings.session.name,
            "remote_control_permission_mode": self._settings.remote_control_permission_mode,
            "agent_teams": self._settings.agent_teams,
            "system_prompt": self._settings.session.system_prompt,
            "initial_prompt": (
                "" if self._has_workflow_trigger() else self._settings.session.initial_prompt
            ),
            "mcp_servers": self._settings.mcp_servers,
            "resume_session_id": self._settings.session.resume_session_id,
            "ask_user_question_enabled": self._settings.ask_user_question_enabled,
            "acp_prompt_timeout_s": self._settings.acp_prompt_timeout_s,
        }

    def _create_codex_auth_provider(self) -> CodexAuthProviderPort:
        """Build the configured auth adapter against Skuld's platform HTTP client."""
        config = self._settings.codex_auth
        cls = import_class(config.adapter)
        kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
        provider = cls(http_client_provider=self._get_http_client, **kwargs)
        if not isinstance(provider, CodexAuthProviderPort):
            raise TypeError(
                f"Codex auth adapter {config.adapter} must implement CodexAuthProviderPort"
            )
        return provider

    def _create_transport(self) -> CLITransport:
        """Create the configured CLI transport via dynamic import.

        Uses ``transport_adapter`` from settings (a fully-qualified class path).
        Legacy ``cli_type`` / ``transport`` fields are resolved to the correct
        adapter path by the config validator before this method is called.
        """
        adapter_path = self._settings.transport_adapter
        if "." not in adapter_path:
            raise ValueError(
                f"Invalid transport_adapter '{adapter_path}': "
                "must be a fully-qualified class path "
                "(e.g. 'skuld.transports.sdk_websocket.SdkWebSocketTransport')"
            )

        try:
            cls = import_class(adapter_path)
        except (ImportError, AttributeError) as exc:
            raise ValueError(f"Cannot load transport adapter '{adapter_path}': {exc}") from exc

        sig = inspect.signature(cls)
        kwargs = self._build_transport_kwargs()
        if "codex_auth_provider" in sig.parameters:
            kwargs["codex_auth_provider"] = self._create_codex_auth_provider()
        filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
        logger.info("Using %s (adapter: %s)", cls.__name__, adapter_path)
        return cls(**filtered)

    async def _auto_start_transport(self) -> None:
        """Background-task wrapper around ``self._transport.start()``.

        Claude subprocess transports do not echo the user's own prompt back as
        an event. Without an explicit synthesis the chat UI sees the
        assistant's reply with no user turn before it. We append a user turn
        to conversation history (so late-joining browsers see it via replay)
        and broadcast ``user_confirmed`` so any already-connected channel
        renders it immediately.
        """
        prompt = self._settings.session.initial_prompt
        if prompt and not any(
            t.role == "user" and t.content == prompt for t in self._conversation_turns
        ):
            turn_id = str(uuid.uuid4())
            self._append_turn(ConversationTurn(id=turn_id, role="user", content=prompt))
            # The initial prompt is a human turn too — persist it to the durable
            # log so log-only replay opens with the operator's first message.
            self._enqueue_human_turn_event(prompt, turn_id)
            try:
                await self._emit_broker_frame(
                    {"type": "user_confirmed", "id": turn_id, "content": prompt}
                )
            except Exception:
                logger.debug("Initial-prompt user_confirmed broadcast failed", exc_info=True)

        try:
            await self._transport.start()
            logger.info("Transport auto-started successfully")
        except Exception:
            logger.error("Transport auto-start failed", exc_info=True)

    async def startup(self) -> None:
        """Initialize the broker on startup."""
        logger.info("Broker starting for session %s", self.session_id)
        logger.info("Transport adapter: %s", self._settings.transport_adapter)
        _rebuild_presented_registry()  # recover present-file cards across broker restarts
        await self._ensure_session_trace_started()

        if self.volundr_api_url:
            logger.info("Token usage reporting enabled: %s", self.volundr_api_url)
        else:
            logger.warning("SKULD__VOLUNDR_API_URL not set — token usage will not be reported")

        # The session is booting — the CLI REPL is not ready yet. Surface
        # ``provisioning`` so clients show "starting up" instead of a misleading
        # ``idle``. The first ``system/init`` event (REPL ready) flips it to
        # ``idle`` (see _handle_cli_event). Fire-and-forget so a slow/absent
        # Volundr never blocks the lifespan from binding the HTTP listener.
        self._set_activity_state("provisioning")
        if self.volundr_api_url:
            asyncio.create_task(self._report_activity_state("provisioning"))

        # Ensure workspace directory exists
        os.makedirs(self.workspace_dir, exist_ok=True)

        # Load conversation history from disk
        self._load_conversation_history()

        # Evict participants whose heartbeats lapse (room mode only)
        if self._room_bridge is not None:
            self._room_bridge.start_presence_sweep()

        # Initialize transport
        self._transport = self._create_transport()
        self._transport.on_event(self._handle_cli_event)

        # Initialize service manager
        self.service_manager = ServiceManager(self.workspace_dir)
        await self.service_manager.init()
        logger.info("Service manager initialized")
        self._git_workspace_checkpoint = _capture_git_workspace_checkpoint(self.workspace_dir)

        # Initialize Telegram channel if configured
        await self._init_telegram_channel()

        # Start chronicle watcher (tails JSONL session files for terminal mode)
        if self._settings.chronicle_watcher_enabled and self.volundr_api_url:
            await self._refresh_workload_token()
            workspace_slug = self.workspace_dir.replace("/", "-")
            watch_dir = Path.home() / ".claude" / "projects" / workspace_slug
            self._chronicle_watcher = ChronicleWatcher(
                session_id=self.session_id,
                watch_dir=watch_dir,
                api_base_url=self.volundr_api_url,
                http_headers=self._build_auth_headers(),
                debounce_ms=self._settings.chronicle_watcher_debounce_ms,
            )
            asyncio.create_task(self._chronicle_watcher.start())
            logger.info("Chronicle watcher started for %s", watch_dir)

        # Start the durable event-log worker (full-fidelity transcript capture)
        await self._init_event_log()

        # Auto-start transport when an initial prompt is configured
        # (dispatched sessions should begin work immediately, not wait
        # for a browser to connect). Run as a background task so the
        # lifespan returns promptly and uvicorn binds — otherwise the
        # transport's first turn (which can take seconds to minutes)
        # blocks the HTTP listener and the chat UI gets 502s.
        if self._is_room_routed_session():
            # Room-routed sessions (flock workflows and residents) have no CLI
            # transport of their own — chat flows to mesh peers / the resident.
            # Warming a transport here would spawn an orphan Claude subprocess
            # alongside the resident, burning tokens and emitting competing
            # frames. A restarted resident always has prior history, so this
            # guard must precede the resume branch below.
            logger.info("Room-routed session — skipping transport auto-start")
        elif self._settings.session.initial_prompt:
            if self._has_workflow_trigger():
                logger.info(
                    "Workflow trigger configured — holding initial prompt for mesh dispatch"
                )
                await self._ensure_workflow_prompt_turn()
            else:
                logger.info("Initial prompt configured — auto-starting transport in background")
                asyncio.create_task(self._auto_start_transport())
        elif self._conversation_turns:
            # Resumed/restarted session (no new initial prompt, but prior history
            # was loaded). Warm the transport eagerly so it is already alive when
            # the next user message arrives. The message-delivery path connects a
            # WebSocket and closes immediately after sending; a cold transport's
            # ~280ms lazy-start outlasts that connection and the first message is
            # dropped (no reply). Warming here makes a restarted session behave
            # like a never-stopped one, where steering works.
            logger.info("Resumed session with prior history — warming transport in background")
            asyncio.create_task(self._auto_start_transport())
        elif "RemoteControl" in (self._settings.transport_adapter or ""):
            # Remote-control sessions take no initial prompt (the native app
            # drives them), so neither branch above fires — but we still want the
            # RC server launched immediately so the pairing URL is ready without
            # waiting for a browser to connect.
            logger.info("Remote-control session — launching the RC server in background")
            asyncio.create_task(self._auto_start_transport())

        # Start mesh adapter if enabled (after transport is ready)
        if self._settings.mesh.enabled:
            await self._start_mesh_adapter()
            if self._has_workflow_trigger():
                # A workflow session is not ready until its kickoff has an
                # acknowledged consumer. Propagate terminal dispatch failure
                # through the ASGI lifespan instead of losing it in a detached
                # task while the session appears healthy.
                await self._run_workflow_trigger_task()
        elif self._has_workflow_trigger():
            logger.warning("Workflow trigger configured but mesh is disabled — skipping dispatch")

        if (
            self._room_bridge is not None
            and self._settings.mesh.enabled
            and self._settings.peer_watchdog.enabled
        ):
            self._peer_watchdog_task = asyncio.create_task(self._peer_watchdog_loop())

        if self._settings.activity_heartbeat.enabled and self.volundr_api_url:
            self._activity_heartbeat_task = asyncio.create_task(self._activity_heartbeat_loop())

    async def shutdown(self) -> None:
        """Clean up on shutdown.

        Reports chronicle summary to Volundr API before stopping the
        transport, so the CLI process is still alive for summary generation.
        """
        logger.info("Broker shutting down")

        if self._room_bridge is not None:
            await self._room_bridge.stop_presence_sweep()

        # Stop chronicle watcher first (flush pending events)
        if self._chronicle_watcher:
            await self._chronicle_watcher.stop()

        # Drain and stop the durable event-log worker so the last turn persists
        await self._stop_event_log()

        if self._activity_heartbeat_task is not None:
            self._activity_heartbeat_task.cancel()
            await asyncio.gather(self._activity_heartbeat_task, return_exceptions=True)
            self._activity_heartbeat_task = None

        if self._peer_watchdog_task is not None:
            self._peer_watchdog_task.cancel()
            await asyncio.gather(self._peer_watchdog_task, return_exceptions=True)
            self._peer_watchdog_task = None

        if self._workflow_trigger_task is not None:
            self._workflow_trigger_task.cancel()
            await asyncio.gather(self._workflow_trigger_task, return_exceptions=True)
            self._workflow_trigger_task = None

        if self._permission_auto_approval_tasks:
            for task in list(self._permission_auto_approval_tasks.values()):
                task.cancel()
            await asyncio.gather(
                *self._permission_auto_approval_tasks.values(),
                return_exceptions=True,
            )
            self._permission_auto_approval_tasks.clear()
        await self._finish_pending_assistant_tool_trace_spans(
            status="cancelled",
            attributes={"reason": "shutdown"},
        )
        await self._finish_trace_span(
            self._trace_assistant_span_id,
            status="cancelled",
            attributes={"reason": "shutdown"},
        )
        self._trace_assistant_span_id = None
        for gate_span_id in list(self._trace_workflow_gate_spans.values()):
            await self._finish_trace_span(
                gate_span_id,
                status="cancelled",
                attributes={"reason": "shutdown"},
            )
        self._trace_workflow_gate_spans.clear()
        for peer_id, tool_span_ids in list(self._trace_peer_tool_spans.items()):
            for tool_span_id in tool_span_ids:
                await self._finish_trace_span(
                    tool_span_id,
                    status="cancelled",
                    attributes={"reason": "shutdown", "peer_id": peer_id},
                )
        self._trace_peer_tool_spans.clear()
        for peer_id, peer_span_id in list(self._trace_peer_turn_spans.items()):
            await self._finish_trace_span(
                peer_span_id,
                status="cancelled",
                attributes={"reason": "shutdown", "peer_id": peer_id},
            )
        self._trace_peer_turn_spans.clear()
        await self._finish_trace_span(
            self._trace_workflow_span_id,
            status="completed",
            attributes={
                "duration_seconds": self._artifacts.duration_seconds,
                "turn_count": self._artifacts.turn_count,
            },
        )
        self._trace_workflow_span_id = None

        # Report chronicle BEFORE stopping the transport (CLI must be alive)
        await self._report_chronicle()
        await self._write_workspace_archive()

        # Stop resident relay and room mesh bridge before mesh adapter
        if self._observation_relay is not None:
            await self._observation_relay.stop()
            self._observation_relay = None

        if self._collaboration_mesh_bridge is not None:
            await self._collaboration_mesh_bridge.stop()
            self._collaboration_mesh_bridge = None

        # Stop mesh adapter before transport (deregister from discovery)
        if self._mesh_adapter is not None:
            await self._mesh_adapter.stop()
            self._mesh_adapter = None

        # Close all message channels (browser WebSockets, Telegram, etc.)
        await self._channels.close_all()

        # Stop transport
        if self._transport:
            await self._transport.stop()

        await self._finish_trace_span(
            self._trace_session_span_id,
            status="completed",
            attributes={
                "duration_seconds": self._artifacts.duration_seconds,
                "turn_count": self._artifacts.turn_count,
                "files_changed": len(self._artifacts.files_changed),
            },
        )
        self._trace_session_span_id = None

        # Close HTTP client
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
