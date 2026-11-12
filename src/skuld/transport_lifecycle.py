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
from personas.domain.persona_document import PersonaDocumentError, PortablePersonaDefinition
from skuld.broker_api import _rebuild_presented_registry, _resume_presented_uploads
from skuld.channels import TelegramChannelError
from skuld.chronicle_watcher import ChronicleWatcher
from skuld.codex_auth import CodexAuthProviderPort
from skuld.conversation_models import ConversationTurn
from skuld.personas.projection import PersonaProjectionError, ProjectionContext
from skuld.personas.registry import projector_for
from skuld.service_manager import ServiceManager
from skuld.session_artifacts import _capture_git_workspace_checkpoint
from skuld.session_runtime import write_private_file

logger = logging.getLogger("skuld.broker")


class TransportLifecycleMixin:
    """Own transport construction and broker startup/shutdown sequencing."""

    def startup_status(self) -> dict[str, object]:
        """Return machine-readable broker startup and readiness state."""
        ready = bool(
            self._startup_ready and not self._startup_failure and self._transport is not None
        )
        return {
            "ready": ready,
            "startup_state": self._startup_state,
            **({"error": self._startup_failure} if self._startup_failure else {}),
            **({"persona": self._persona_status} if self._persona_status else {}),
        }

    def _fail_startup(self, exc: BaseException) -> None:
        """Record a terminal startup failure without taking down diagnostics."""
        message = str(exc).strip() or type(exc).__name__
        self._startup_ready = False
        self._startup_state = "failed"
        self._startup_failure = f"{type(exc).__name__}: {message}"[:2000]
        self._set_activity_state("error")

    def _report_startup_failure(self) -> None:
        """Tell Forge the broker cannot start, so a session still coming up fails now.

        Without it Forge sees only a pod that never turns ready, and a run waiting on the
        session learns of the failure when the platform gives up on the install. Fire-and-
        forget like the provisioning report: the diagnostics listener must still bind.
        """
        if not self.volundr_api_url:
            return
        asyncio.create_task(
            self._report_activity_state(
                "error",
                extra_metadata={"error": self._startup_failure, "startup_failed": True},
            )
        )

    def _build_transport_kwargs(self) -> dict:
        """Return superset of kwargs that any transport constructor might need."""
        return {
            "workspace_dir": self.workspace_dir,
            "model": self.model,
            "reasoning_effort": self._restored_effort(),
            "service_tier": getattr(self, "_runtime_service_tier", None),
            "effort_control_timeout_s": self._settings.effort_control_timeout_s,
            "sdk_port": self._settings.port,
            "session_id": self.session_id,
            "skip_permissions": self._settings.skip_permissions,
            "approval_policy": self._settings.approval_policy,
            "sandbox": self._settings.sandbox,
            "cli_binary": self._settings.cli_binary,
            "session_name": self._settings.session.name,
            "remote_control_permission_mode": self._settings.remote_control_permission_mode,
            "claude_permission_mode": self._settings.claude_permission_mode,
            "agent_teams": self._settings.agent_teams,
            "system_prompt": self._settings.session.system_prompt,
            "initial_prompt": self._settings.session.initial_prompt,
            # Configured servers plus the built-in forge MCP (merged by name).
            "mcp_servers": self._effective_mcp_servers(),
            "session_tools": self._session_tools(),
            "resume_session_id": self._settings.session.resume_session_id,
            "ask_user_question_enabled": self._settings.ask_user_question_enabled,
            "acp_prompt_timeout_s": self._settings.acp_prompt_timeout_s,
            "muse_bin": self._settings.muse_bin,
            "pi_bin": self._settings.pi.binary,
            "pi_agent_dir": self._settings.pi.agent_dir,
            "pi_session_dir": self._settings.pi.session_dir,
            "pi_command_timeout_s": self._settings.pi.command_timeout_s,
            "pi_turn_timeout_s": self._settings.pi.turn_timeout_s,
            "pi_shutdown_timeout_s": self._settings.pi.shutdown_timeout_s,
            "question_transcript_max_bytes": self._settings.tmux_question_transcript_max_bytes,
            "workspace_trust_stable_s": self._settings.tmux_workspace_trust_stable_s,
            "workspace_trust_settle_s": self._settings.tmux_workspace_trust_settle_s,
            "workspace_trust_max_attempts": self._settings.tmux_workspace_trust_max_attempts,
            "question_result_history_limit": self._settings.tmux_question_result_history_limit,
            "native_text_wait_s": self._settings.tmux_native_text_wait_s,
            "native_text_poll_s": self._settings.tmux_native_text_poll_s,
            "codex_receive_max_bytes": self._settings.codex_receive_max_bytes,
            "live_frame_max_bytes": self._settings.live_frame_max_bytes,
            "dsh_runtime_bin": self._settings.dsh.runtime_bin,
            "dsh_cordis_config": self._settings.dsh.cordis_config,
            "dsh_base_url": self._settings.dsh.base_url,
            "dsh_api_key": self._settings.dsh.api_key,
            "dsh_provider": self._settings.dsh.provider,
            "dsh_prompt_timeout_s": self._settings.dsh.prompt_timeout_s,
            "claude_auth": self._settings.claude_auth,
            "claude_oauth_token_file": self._settings.claude_oauth_token_file,
            "tmux_remote_control": self._settings.tmux_remote_control,
            "model_gateway_url": self._settings.model_gateway.url,
            "model_gateway_token": self._settings.model_gateway.token,
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
        self._restore_runtime_options()
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
        self._project_persona(cls, adapter_path, kwargs)
        filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
        logger.info("Using %s (adapter: %s)", cls.__name__, adapter_path)
        if "claude_permission_mode" in sig.parameters:
            self._log_claude_permission_mode()
        return cls(**filtered)

    def _project_persona(self, cls: type, adapter_path: str, kwargs: dict) -> None:
        """Play the session's persona on this transport, or refuse to start.

        The projection is a pure function of the pinned persona, the request's ad-hoc prompt
        and this node's configuration, so every start of the session, resume included, builds
        the transport the same way. Raises :class:`PersonaProjectionError` when the harness
        cannot enforce something the persona states, or when a projected argument is not one
        the transport accepts (a projection that would be dropped silently).
        """
        raw = self._settings.session.persona
        if raw is None:
            return
        persona = PortablePersonaDefinition.from_dict(raw)
        context = ProjectionContext(
            transport=adapter_path,
            runtime_dir=self._session_runtime.root,
            request_prompt=self._settings.session.system_prompt,
            mcp_servers=tuple(kwargs.get("mcp_servers") or ()),
            node_skip_permissions=self._settings.skip_permissions,
            node_claude_permission_mode=self._settings.claude_permission_mode,
            node_approval_policy=self._settings.approval_policy,
            node_sandbox=self._settings.sandbox,
        )
        projection = projector_for(adapter_path).project(persona, context)
        projection.require_playable(persona.id, adapter_path)
        accepted = {
            name
            for name, parameter in inspect.signature(cls).parameters.items()
            if parameter.kind not in (parameter.VAR_KEYWORD, parameter.VAR_POSITIONAL)
        }
        unaccepted = sorted(set(projection.kwargs) - accepted)
        if unaccepted:
            raise PersonaProjectionError(
                f"Persona {persona.id!r} projects {', '.join(unaccepted)} onto {adapter_path}, "
                "whose constructor does not accept it, so the persona's limits would be "
                "dropped. Add the argument to the transport or use a transport that has it."
            )
        kwargs.update(projection.kwargs)
        for projected in projection.files:
            write_private_file(self._session_runtime.root / projected.name, projected.content)
        for note in projection.notes:
            logger.warning(
                "Persona %s on %s: %s (%s)",
                persona.id,
                projection.engine,
                note.message,
                note.subject,
            )
        self._persona_status = {
            "id": persona.id,
            "revision": persona.revision,
            "engine": projection.engine,
            "projected": sorted(projection.kwargs),
            "notes": [gap.describe() for gap in projection.notes],
        }
        logger.info(
            "Persona %s@%s projected onto %s (%s)",
            persona.id,
            persona.revision,
            projection.engine,
            ", ".join(sorted(projection.kwargs)),
        )

    def _log_claude_permission_mode(self) -> None:
        """Say which permission mode Claude gets and whether the node chose it."""
        mode = self._settings.claude_permission_mode
        if "claude_permission_mode" in self._settings.model_fields_set:
            logger.info("Claude permission mode: %s (set by this node)", mode)
            return
        logger.warning(
            "Claude permission mode: %s (default; set claude_permission_mode in the node config)",
            mode,
        )

    async def _ensure_transport_started(self) -> None:
        """Join any in-flight startup before accepting messages on any channel."""
        async with self._transport_start_lock:
            if self._transport is None:
                raise RuntimeError("Transport not initialized")
            if not self._transport.is_alive:
                await self._transport.start()

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
            await self._ensure_transport_started()
            logger.info("Transport auto-started successfully")
        except Exception:
            logger.error("Transport auto-start failed", exc_info=True)

    def _start_preview(self) -> None:
        """Start live-preview port discovery and forwarding when configured."""
        if not self._settings.preview.enabled:
            return
        from skuld.preview import build_preview

        self.preview_watcher, self.preview_forwarder = build_preview(
            self._settings.preview,
            session_id=self.session_id,
            emit=self._emit_broker_frame,
        )
        self.preview_watcher.start()
        logger.info(
            "Live preview enabled: scanning listening ports every %ss",
            self._settings.preview.port_scan_interval_seconds,
        )

    async def startup(self) -> None:
        """Initialize the broker on startup."""
        self._startup_ready = False
        self._startup_state = "starting"
        self._startup_failure = ""
        logger.info("Broker starting for session %s", self.session_id)
        logger.info("Transport adapter: %s", self._settings.transport_adapter)
        if self._settings.creates_session_home:
            # Present-file stages under home; without it every present fails to stage.
            os.makedirs(self._settings.home_path, exist_ok=True)
        _rebuild_presented_registry()  # recover present-file cards across broker restarts
        _resume_presented_uploads()  # F2: finish durable uploads cut off by the restart
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
        self._activity_report_pending = True
        if self.volundr_api_url:
            asyncio.create_task(self._report_activity_state("provisioning"))

        # Ensure workspace directory exists
        os.makedirs(self.workspace_dir, exist_ok=True)

        # Load conversation history from disk
        self._load_control_state()
        self._load_conversation_history()
        await self._hydrate_conversation_history()

        # Show participants whose heartbeats lapse as away (room mode only)
        if self._room_bridge is not None:
            self._room_bridge.start_presence_sweep()

        # Initialize transport
        try:
            self._transport = self._create_transport()
        except (PersonaProjectionError, PersonaDocumentError) as exc:
            # The harness cannot play the session's persona. Keep the HTTP diagnostics alive
            # so /ready reports exactly why, and start nothing that would run unrestricted.
            self._fail_startup(exc)
            logger.critical(
                "Persona cannot be played on this transport; broker remains unready: %s",
                self._startup_failure,
                exc_info=True,
            )
            self._report_startup_failure()
            return
        self._transport.on_event(self._handle_cli_event)

        # Initialize service manager
        self.service_manager = ServiceManager(self.workspace_dir)
        await self.service_manager.init()
        logger.info("Service manager initialized")
        self._start_preview()
        self._git_workspace_checkpoint = _capture_git_workspace_checkpoint(self.workspace_dir)

        # Initialize Telegram channel if configured. A configured channel that cannot start
        # fails the session: it would otherwise run unreachable from the operator's chat.
        try:
            await self._init_telegram_channel()
        except TelegramChannelError as exc:
            self._fail_startup(exc)
            logger.critical(
                "Configured Telegram channel cannot start; broker remains unready: %s",
                self._startup_failure,
                exc_info=True,
            )
            self._report_startup_failure()
            return

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
            # Room-routed sessions (residents) have no CLI
            # transport of their own — chat flows to the resident.
            # Warming a transport here would spawn an orphan Claude subprocess
            # alongside the resident, burning tokens and emitting competing
            # frames. A restarted resident always has prior history, so this
            # guard must precede the resume branch below.
            logger.info("Room-routed session — skipping transport auto-start")
        elif self._settings.session.initial_prompt:
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
            try:
                await self._start_mesh_adapter()
            except Exception as exc:
                self._fail_startup(exc)
                logger.critical(
                    "Configured mesh startup failed; broker remains unready: %s",
                    self._startup_failure,
                    exc_info=True,
                )
                self._report_startup_failure()
                # Keep the HTTP diagnostics surface alive so the pod manager can
                # observe the exact terminal failure through /ready, persist it,
                # and tear down the runtime.  Nothing below this point should
                # start for a broker whose configured mesh never came up.
                return

        if self._settings.activity_heartbeat.enabled and self.volundr_api_url:
            self._activity_heartbeat_task = asyncio.create_task(self._activity_heartbeat_loop())

        if not self._startup_failure:
            self._startup_ready = True
            self._startup_state = "ready"
            if self._activity_state == "provisioning" and (
                self._is_room_routed_session() or not self._transport.capabilities.announces_ready
            ):
                # The ``system/init`` frame that normally leaves provisioning does
                # not come on its own: room-routed sessions never start a CLI
                # transport, and some CLIs announce themselves only after their
                # first message. Startup is complete; the session takes that
                # message now, so it is idle, not starting.
                asyncio.create_task(self._report_activity_state("idle"))
            if self._settings.workflow_steps.enabled or self._peer_messages_waiting():
                # A step or peer message restored from the durable log that never reached the
                # harness is handed over now; one already handed over is left to its turn.
                self._spawn_boundary_injection("restore-inject")

    async def shutdown(self) -> None:
        """Clean up on shutdown.

        Reports chronicle summary to Volundr API before stopping the
        transport, so the CLI process is still alive for summary generation.
        """
        if self._shutdown_started:
            return
        self._shutdown_started = True
        logger.info("Broker shutting down")
        self._startup_ready = False
        self._startup_state = "stopping"

        if self._room_bridge is not None:
            await self._room_bridge.stop_presence_sweep()

        if self.preview_watcher is not None:
            await self.preview_watcher.stop()
        if self.preview_forwarder is not None:
            await self.preview_forwarder.aclose()

        # Stop chronicle watcher first (flush pending events)
        if self._chronicle_watcher:
            await self._chronicle_watcher.stop()

        # Drain and stop the durable event-log worker so the last turn persists
        await self._stop_event_log()

        if self._activity_heartbeat_task is not None:
            self._activity_heartbeat_task.cancel()
            await asyncio.gather(self._activity_heartbeat_task, return_exceptions=True)
            self._activity_heartbeat_task = None

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

        # The loopback secret dies with this broker start.
        self._revoke_session_runtime()

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
        if self._session_http_client:
            await self._session_http_client.aclose()
            self._session_http_client = None
