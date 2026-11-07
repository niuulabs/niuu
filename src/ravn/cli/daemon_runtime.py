"""Focused runtime implementation extracted from :mod:`ravn.cli.commands`."""

from __future__ import annotations

# Dependencies are supplied by the CLI compatibility facade immediately before
# invocation so established command patch points remain effective.
# ruff: noqa: F821


async def _run_daemon(
    settings: Settings,
    *,
    persona_config: Any | None = None,
    profile: RavnProfile | None = None,
    task_dispatch: bool = False,
    resume: bool = False,
) -> None:
    """Build and run the gateway + drive loop until interrupted."""
    from niuu.observability import configure_observability, shutdown_observability
    from ravn.adapters.channels.gateway import RavnGateway
    from ravn.adapters.channels.gateway_http import HttpGateway
    from ravn.adapters.channels.gateway_telegram import TelegramGateway
    from ravn.adapters.executors.cli_threads import (  # noqa: PLC0415
        CliThreadStore,
        workflow_thread_key,
    )
    from ravn.drive_loop import DriveLoop
    from ravn.ports.channel import ChannelPort

    configure_observability(
        settings.observability,
        resource_attributes={
            "service.instance.id": settings.mesh.own_peer_id or settings.environment.id,
            "deployment.environment.name": settings.environment.id,
            "ravn.environment.id": settings.environment.id,
            "ravn.environment.type": settings.environment.type,
            "ravn.runtime.component": "resident",
        },
    )

    if profile is not None:
        system_prompt, max_iterations, _max_tokens_d = _apply_profile(
            profile, settings, persona_config=persona_config
        )
    else:
        system_prompt = settings.agent.system_prompt
        max_iterations = settings.agent.max_iterations
        if persona_config is not None:
            if persona_config.system_prompt_template:
                system_prompt = persona_config.system_prompt_template
            if persona_config.iteration_budget is not None:
                max_iterations = persona_config.iteration_budget
    base_model = _resolve_persona_model(settings, persona_config)

    workspace = _resolve_workspace(settings)
    from ravn.cli.tool_builders import _build_learned_tool_resolver  # noqa: PLC0415

    learned_tool_resolver = _build_learned_tool_resolver(settings, workspace)
    cli_transport_executor = _uses_cli_transport_executor(persona_config)
    llm = None if cli_transport_executor else _build_llm(settings)
    memory = _build_memory(settings)
    # Built here for the persona's own model so an impossible compaction setting
    # fails at startup; each task builds its own for the model it runs on.
    if llm is not None:
        _build_compaction(settings, llm, base_model)
    pre_hooks, post_hooks = _build_hooks(settings)

    mcp_manager: Any | None = None
    mcp_tools: list[Any] = []

    # Build Mímir adapter early so _agent_factory closure can capture it.
    daemon_mimir = build_mimir(settings)
    memory = _with_mimir_fact_capture(memory, daemon_mimir)

    drive_loop: Any | None = None

    # What the resident wants its owner to hear about (questions it waits on, its
    # agent's own notifications). Forge drains it through the OpenClaw shim.
    from ravn.cli.voice_wiring import build_resident_notification_outbox  # noqa: PLC0415

    resident_notification_outbox = build_resident_notification_outbox(settings)
    # The call requests notify's speak queues for the voice channel (voice.calls).
    resident_call_requests = None
    if resident_notification_outbox is not None:
        from ravn.cli.voice_wiring import build_resident_call_requests  # noqa: PLC0415

        resident_call_requests = build_resident_call_requests(settings)

    # Populated by _wire_cron after drive_loop is created; captured by _agent_factory.
    cron_tools: list[Any] = []

    sleipnir_catalog_publisher: Any | None = None

    # This publisher is shared by gateways and the drive loop. Start it before
    # constructing any of those producers so an immediately-ready trigger cannot
    # publish against an unstarted adapter.
    environment_signal_publisher_started = False
    environment_signal_publisher: Any | None = _build_environment_signal_publisher(settings)
    if environment_signal_publisher is not None and hasattr(
        environment_signal_publisher,
        "start",
    ):
        await environment_signal_publisher.start()
        environment_signal_publisher_started = True

    # Only a workflow session: its state dir is on that session's own workspace and it
    # runs one workflow. A long-lived process would carry threads from run to run.
    workflow_thread_store = (
        CliThreadStore(_resident_ravn_state_dir(workspace, settings) / "cli_threads.json")
        if settings.workflow.graph
        else None
    )

    def _agent_factory(
        channel: ChannelPort,
        task_id: str | None = None,
        task_persona: str | None = None,
        triggered_by: str | None = None,
    ) -> Any:
        # Resolve per-task persona — triggered tasks may request a different persona.
        resolved_persona = persona_config
        resolved_system_prompt = system_prompt
        resolved_max_iterations = max_iterations
        resolved_max_tokens = settings.effective_max_tokens()
        resolved_model = base_model
        if task_persona and task_persona != (persona_config.name if persona_config else None):
            task_persona_cfg = _resolve_persona(task_persona, None, settings=settings)
            if task_persona_cfg is not None:
                resolved_persona = task_persona_cfg
                resolved_model = _resolve_persona_model(settings, resolved_persona)
                if task_persona_cfg.system_prompt_template:
                    resolved_system_prompt = task_persona_cfg.system_prompt_template
                if task_persona_cfg.iteration_budget is not None:
                    resolved_max_iterations = task_persona_cfg.iteration_budget
                if task_persona_cfg.llm.max_tokens:
                    resolved_max_tokens = task_persona_cfg.llm.max_tokens

        session = Session()
        budget = _build_iteration_budget(settings, resolved_max_iterations)
        permission = _build_permission(
            settings,
            workspace,
            no_tools=False,
            persona_config=resolved_persona,
        )
        permission_mode = _effective_permission_mode(settings, resolved_persona)

        # Determine the profile for this task:
        #   - Anonymous cascade subtasks (no persona, no task_persona) → "worker" (core only)
        #   - Tasks on a node with a configured persona → derive include_groups from
        #     the resolved persona's allowed_tools so the tool set matches exactly
        #     what the persona permits, regardless of how the task was triggered.
        #   - Everything else → "default"
        is_anonymous_cascade = task_id is not None and not task_persona and resolved_persona is None
        profile = "worker" if is_anonymous_cascade else "default"
        profile_cfg = _get_tool_group(settings, profile)

        async def _emit_mimir_ingest_event(
            event_type: str,
            fields: dict[str, Any],
        ) -> None:
            if drive_loop is None or resolved_persona is None:
                return
            current_task = drive_loop.resolve_task_context(task_id)
            if current_task is None:
                logger.warning(
                    "mimir_ingest: no task context available for persona=%s task_id=%s",
                    resolved_persona.name,
                    task_id,
                )
                return
            drive_loop.record_tool_outcome_fields(
                task=current_task,
                event_type=event_type,
                fields=fields,
            )

        tools = _build_tools(
            settings,
            workspace,
            session,
            llm,
            memory,
            budget,
            mimir=daemon_mimir,
            persona_config=resolved_persona,
            profile=profile,
            discovery=_cascade_participant.discovery if _cascade_participant is not None else None,
            mimir_event_emitter=(
                _emit_mimir_ingest_event if resolved_persona is not None else None
            ),
            session_join_manager=(
                drive_loop._session_join_manager if drive_loop is not None else None
            ),
            permission=permission,
            learned_tool_resolver=learned_tool_resolver,
        )
        if profile_cfg.include_mcp:
            tools.extend(_filter_tools(mcp_tools, settings, resolved_persona))
        if "cascade" in profile_cfg.include_groups:
            # Add cascade tools (parallel task execution) if wired
            # NOTE: cascade_tools uses the same monkey-patch pattern as before —
            # tracked as tech debt to align with the cron pattern.
            # NIU-612: Apply persona's allowed_tools filter to cascade/cron tools.
            cascade_tools = getattr(drive_loop, "_cascade_tools", [])
            tools.extend(_filter_tools(cascade_tools, settings, resolved_persona))

        # Cron is temporal agency, not a cascade capability. Exact persona tool
        # names still control which scheduler operations enter this task.
        if cron_tools:
            tools.extend(_filter_tools(cron_tools, settings, resolved_persona))

        # A persona presents documents only when it lists present_file (its own judgment
        # of what a person should read); the tool exists only where Forge configured it.
        present_file_tool = getattr(drive_loop, "_present_file_tool", None)
        if present_file_tool is not None:
            tools.extend(_filter_tools([present_file_tool], settings, resolved_persona))

        # Like a Forge session's notify, offered to every persona wherever the outbox
        # exists; only an explicit disable or a persona's forbidden_tools drops it.
        if resident_notification_outbox is not None and _notify_tool_allowed(
            settings, resolved_persona
        ):
            from ravn.adapters.tools.notify import ResidentNotifyTool  # noqa: PLC0415

            tools.append(
                ResidentNotifyTool(resident_notification_outbox, calls=resident_call_requests)
            )

        # NIU-571: Apply trust gradient constraints for thread-triggered tasks
        tools = _apply_trust_filter(tools, settings, triggered_by)
        # A CLI agent's tool server reaches the broker through the daemon's relay.
        present_file_relay_socket = _present_file_relay_socket(tools, present_file_relay)

        executor = _build_executor(resolved_persona)
        resolved_extended_thinking = _resolve_extended_thinking(
            settings,
            resolved_persona,
            cli_transport_executor=_uses_cli_transport_executor(resolved_persona),
        )
        compressor, native_compaction = (
            _build_compaction(settings, llm, resolved_model) if llm is not None else (None, None)
        )
        agent = executor.build(
            llm=llm,
            tools=tools,
            channel=channel,
            permission=permission,
            permission_mode=permission_mode,
            system_prompt=resolved_system_prompt,
            model=resolved_model,
            max_tokens=resolved_max_tokens,
            max_iterations=resolved_max_iterations,
            workspace_dir=str(workspace),
            mcp_servers=_transport_mcp_servers(settings),
            present_file_relay=present_file_relay_socket,
            session=session,
            pre_tool_hooks=pre_hooks or None,
            post_tool_hooks=post_hooks or None,
            user_input_fn=None,
            memory=memory,
            mimir=daemon_mimir,
            episode_summary_max_chars=settings.agent.episode_summary_max_chars,
            episode_task_max_chars=settings.agent.episode_task_max_chars,
            iteration_budget=budget,
            compressor=compressor,
            native_compaction=native_compaction,
            prompt_builder=_build_prompt_builder(settings),
            reflection_model=settings.effective_memory_reflection_model(),
            reflection_max_tokens=settings.memory.reflection_max_tokens,
            task_summary_max_chars=settings.memory.task_summary_max_chars,
            input_token_cost_per_million=settings.memory.input_token_cost_per_million,
            output_token_cost_per_million=settings.memory.output_token_cost_per_million,
            extended_thinking=resolved_extended_thinking,
            persona=resolved_persona.name if resolved_persona else "",
            # Persona config drives outcome parsing; stop_on_outcome is retained
            # only for compatibility and cannot suppress tool execution.
            persona_config=resolved_persona,
            stop_on_outcome=resolved_persona.stop_on_outcome if resolved_persona else False,
            # NIU-1118: hard per-call prompt budget (0 disables)
            max_prompt_tokens=settings.context_management.max_prompt_tokens,
            context_window_tokens=settings.context_management.context_window_tokens,
            token_estimate_safety_factor=(settings.context_management.token_estimate_safety_factor),
            # NIU-1118: bound one tool result's contribution to history
            max_tool_result_chars=settings.tools.max_result_chars,
            session_join_manager=(
                drive_loop._session_join_manager if drive_loop is not None else None
            ),
            workflow_thread_store=workflow_thread_store,
            # Resolved when the turn starts: the task is not active while its agent is built.
            workflow_thread_key=lambda: workflow_thread_key(
                drive_loop.resolve_task_context(task_id) if drive_loop is not None else None
            ),
        )
        return agent

    tasks: list[asyncio.Task] = []

    # Create interaction tracker early so it can be shared between gateway
    # channels (touch on operator message) and the Mímir recap trigger (read).
    from ravn.domain.interaction_tracker import LastInteractionTracker

    interaction_tracker = LastInteractionTracker()

    # Gateway channels (human-initiated turns)
    gw_tasks: list[str] = []
    http_gateway: Any | None = None
    gw: RavnGateway | None = None
    channels_cfg = settings.gateway.channels
    _any_channel = (
        channels_cfg.telegram.enabled
        or channels_cfg.http.enabled
        or channels_cfg.discord.enabled
        or channels_cfg.slack.enabled
        or channels_cfg.matrix.enabled
        or channels_cfg.whatsapp.enabled
        or getattr(channels_cfg, "openclaw", None) is not None
        and channels_cfg.openclaw.enabled
        or channels_cfg.voice.enabled
    )
    if _any_channel:
        gw = RavnGateway(
            settings.gateway,
            _agent_factory,
            profile=profile,
            interaction_tracker=interaction_tracker,
        )

        if channels_cfg.telegram.enabled:
            tg = TelegramGateway(channels_cfg.telegram, gw)
            tasks.append(asyncio.create_task(tg.run(), name="telegram"))
            gw_tasks.append("telegram")

        if channels_cfg.http.enabled:
            http_gateway = HttpGateway(channels_cfg.http, gw)
            tasks.append(asyncio.create_task(http_gateway.run(), name="http"))
            gw_tasks.append("http")

        def _voice_factory() -> Any:
            from ravn.cli.voice_wiring import build_voice_gateway  # noqa: PLC0415

            return build_voice_gateway(settings, persona_config)

        for task, name in _make_channel_tasks(
            channels_cfg,
            gw,
            notifications=resident_notification_outbox,
            voice_factory=_voice_factory,
        ):
            tasks.append(task)
            gw_tasks.append(name)

    # Drive loop (initiative tasks)
    from ravn.adapters.events.noop_publisher import NoOpEventPublisher
    from ravn.adapters.events.rabbitmq_publisher import RabbitMQEventPublisher
    from ravn.ports.event_publisher import EventPublisherPort

    event_publisher: EventPublisherPort = NoOpEventPublisher()
    trigger_names: list[str] = []
    drive_loop: Any = None
    _cascade_participant: Any = None
    present_file_relay: Any = None
    huddle_archiver: Any | None = None
    if settings.initiative.enabled or task_dispatch:
        if settings.sleipnir.enabled:
            event_publisher = RabbitMQEventPublisher(settings.sleipnir)
            amqp_url = os.environ.get(settings.sleipnir.amqp_url_env, "").strip()
            if amqp_url:
                try:
                    from sleipnir.adapters.rabbitmq import RabbitMQPublisher

                    sleipnir_catalog_publisher = RabbitMQPublisher(
                        url=amqp_url,
                        exchange_name=settings.sleipnir.exchange,
                    )
                    if settings.observability.enabled:
                        from ravn.adapters.observability import (  # noqa: PLC0415
                            ObservedSleipnirBus,
                        )

                        sleipnir_catalog_publisher = ObservedSleipnirBus(sleipnir_catalog_publisher)
                    await sleipnir_catalog_publisher.start()
                except Exception as exc:
                    logger.warning(
                        "sleipnir: catalog publisher unavailable; "
                        "catalog events will not be published: %s",
                        exc,
                    )
                    sleipnir_catalog_publisher = None
            else:
                logger.warning(
                    "sleipnir: %s is not set; catalog events will not be published",
                    settings.sleipnir.amqp_url_env,
                )

        from ravn.api.persistence_wiring import build_resident_budget  # noqa: PLC0415

        _resident_budget = build_resident_budget(
            settings.resident_budget,
            default_timeout_seconds=settings.gateway.platform.timeout,
        )

        drive_loop = DriveLoop(
            agent_factory=_agent_factory,
            config=settings.initiative,
            settings=settings,
            event_publisher=event_publisher,
            resume=resume,
            resident_budget=_resident_budget,
            mimir=daemon_mimir,
            sleipnir_publisher=environment_signal_publisher or sleipnir_catalog_publisher,
        )
        if hasattr(drive_loop, "set_persona_config"):
            drive_loop.set_persona_config(persona_config)
        if hasattr(drive_loop, "register_telemetry_refresh"):
            # The inventory is published once while the resolver is built, so
            # the gauge ages out and the fleet reads zero installed tools while
            # hundreds sit on disk. Re-state it on every heartbeat.
            def _refresh_learned_tool_inventory() -> None:
                from ravn.tool_observability import (  # noqa: PLC0415
                    publish_learned_tool_inventory,
                )

                publish_learned_tool_inventory(learned_tool_resolver.list_loadable_artifacts())

            drive_loop.register_telemetry_refresh(_refresh_learned_tool_inventory)
        _cron_jobs = _wire_triggers(drive_loop, settings.initiative)
        cron_tools[:] = _wire_cron(drive_loop, _cron_jobs, settings.initiative)

        # Load this resident's own durably-stored triggers (POST /ravn/triggers)
        # and run them — gated by resident_triggers.enabled (see trigger_wiring).
        _api_triggers_persona = (
            persona_config.name
            if persona_config is not None
            else settings.initiative.default_persona
        )
        _wire_api_triggers(drive_loop, settings, _api_triggers_persona)

        # Wire Mímir triggers (source synthesis + staleness refresh + threads)
        if daemon_mimir is not None:
            _wire_mimir_triggers(
                drive_loop,
                daemon_mimir,
                settings,
                llm=llm,
                interaction_tracker=interaction_tracker,
                agent_factory=_agent_factory,
                gateway=gw,
            )

        # Wire task dispatch subscription when requested (--listen / --daemon)
        if task_dispatch and settings.sleipnir.enabled:
            _wire_task_dispatch(drive_loop, settings.sleipnir)
        elif task_dispatch:
            logger.warning(
                "task_dispatch: Sleipnir not enabled — ravn.task.dispatch subscription"
                " requires sleipnir.enabled: true and %s to be set",
                settings.sleipnir.amqp_url_env,
            )

        # Wire cascade tools when enabled (Mode 1 local + optional mesh/spawn)
        if settings.cascade.enabled:
            _active_profile_name = profile.name if profile else "default"
            _cascade_participant = _wire_cascade(
                drive_loop, settings, persona_config, _active_profile_name
            )
            if _cascade_participant is not None:
                await _cascade_participant.start()
                _cascade_mesh = _cascade_participant.mesh
                pending = getattr(_cascade_mesh, "_pending_outcome_subscriptions", [])
                for event_type, handler in pending:
                    logger.info("mesh: subscribing to event_type=%s", event_type)
                    await _cascade_mesh.subscribe(event_type, handler)
                present_file_relay = await _start_present_file_relay(settings, _cascade_mesh)

        trigger_names = [t.name for t in drive_loop._triggers]
        tasks.append(asyncio.create_task(drive_loop.run(), name="drive_loop"))

    if (
        daemon_mimir is not None
        and environment_signal_publisher is not None
        and hasattr(environment_signal_publisher, "subscribe")
        and settings.environment.flocks
    ):
        from ravn.huddle_archiver import HuddleTranscriptArchiver  # noqa: PLC0415

        huddle_archiver = HuddleTranscriptArchiver(
            subscriber=environment_signal_publisher,
            mimir=daemon_mimir,
        )
        await huddle_archiver.start()
        typer.echo("  Huddle transcript archiver: subscribed")

    async def _handle_mcp_tool_result(
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> None:
        if tool_name not in {"mimir_ingest", "mimir_write"} or drive_loop is None:
            return
        current_task = drive_loop.resolve_task_context()
        if current_task is None:
            logger.warning(
                "%s MCP hook: no task context available for server=%s",
                tool_name,
                server_name,
            )
            return
        if tool_name == "mimir_ingest":
            fields = _mimir_ingest_event_fields_from_mcp_result(
                server_name=server_name,
                arguments=arguments,
                result=result,
            )
            if fields is None:
                return
            drive_loop.record_tool_outcome_fields(
                task=current_task,
                event_type="mimir.source.ingested",
                fields=fields,
            )
            return

        fields = _mimir_write_event_fields_from_mcp_result(
            server_name=server_name,
            arguments=arguments,
            result=result,
        )
        if fields is None:
            return
        drive_loop.record_tool_outcome_fields(
            task=current_task,
            event_type="mimir.page.written",
            fields=fields,
        )

    mcp_manager, mcp_tools = await _start_mcp_shared(
        settings,
        tool_result_hook=_handle_mcp_tool_result,
    )

    channels_str = ", ".join(gw_tasks) if gw_tasks else "none"
    triggers_str = ", ".join(trigger_names) if trigger_names else "none"
    concurrent = settings.initiative.max_concurrent_tasks if settings.initiative.enabled else 0

    typer.echo("ravn daemon started.")
    typer.echo(f"  Channels: {channels_str}")
    typer.echo(f"  Triggers: {triggers_str}")
    typer.echo(
        f"  Drive loop: {'running' if settings.initiative.enabled else 'disabled'}"
        + (f" (max {concurrent} concurrent tasks)" if settings.initiative.enabled else "")
    )
    typer.echo("Press Ctrl+C to stop.")

    if not tasks:
        # Nothing runs, so the gather below returns at once and the one shutdown
        # in its ``finally`` releases everything that was built. A second,
        # hand-copied shutdown here had drifted: it skipped memory, the event
        # publisher, MCP and the notification outbox.
        typer.echo("No channels or triggers enabled — daemon has nothing to do.", err=True)

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        return
    except KeyboardInterrupt:
        return
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if present_file_relay is not None:
            await present_file_relay.stop()
        if _cascade_participant is not None:
            await _cascade_participant.stop()
        if huddle_archiver is not None:
            await huddle_archiver.stop()
        if environment_signal_publisher_started and environment_signal_publisher is not None:
            await environment_signal_publisher.stop()
        if resident_notification_outbox is not None:
            # Every task and trigger above has stopped, so nothing writes to the
            # outbox any more; its SQLite connection is released, not leaked.
            resident_notification_outbox.close()
        if resident_call_requests is not None:
            resident_call_requests.close()
        if memory is not None:
            # MemoryPort.close() is a no-op for file/sqlite backends and closes
            # the connection pool for postgres. Called unconditionally: the
            # composition root should not have to know which backend it wired.
            await _shutdown_step("memory.close", memory.close)
        await event_publisher.close()
        await _shutdown_mcp(mcp_manager)
        if sleipnir_catalog_publisher is not None:
            await sleipnir_catalog_publisher.stop()
        shutdown_observability()


async def _shutdown_step(name: str, step: Any) -> None:
    """Run one shutdown step; a failure is reported and the shutdown goes on.

    Raising here would skip every later step (closing connections, flushing the
    rest), so this is the one place a failure continues. It is never silent: it
    logs at error with the traceback and counts as degraded ``shutdown.<name>``.
    """
    from ravn.cap_telemetry import record_degraded  # noqa: PLC0415

    try:
        await step()
    except Exception as exc:  # noqa: BLE001 - reported and counted below
        logger.error("daemon shutdown: %s failed", name, exc_info=True)
        record_degraded(f"shutdown.{name}", f"{name} failed during shutdown: {exc}")


async def _start_present_file_relay(settings: Any, mesh: Any) -> Any | None:
    """Serve ``present_file`` to the daemon's CLI agents' tool servers, when configured.

    Their ``ravn tool-mcp`` process cannot join the flock mesh, so it presents through
    this relay, which forwards to the broker over the daemon's own mesh connection.
    """
    if not settings.present_file.enabled:
        return None
    from ravn.adapters.tools.present_file_relay import PresentFileRelayServer  # noqa: PLC0415

    relay = PresentFileRelayServer(
        mesh,
        broker_peer_id=settings.present_file.broker_peer_id,
        timeout_s=settings.present_file.timeout_s,
    )
    await relay.start()
    return relay


def _present_file_relay_socket(tools: list[Any], relay: Any | None) -> str:
    """The relay socket a CLI agent's tool server presents through, when it has the tool.

    The tool exists only with the flock mesh, and the daemon starts the relay with that
    mesh, so a task offered ``present_file`` without a relay is a wiring fault: raised,
    never a CLI agent quietly missing the tool.
    """
    from ravn.adapters.tools.present_file import PRESENT_FILE_TOOL_NAME  # noqa: PLC0415

    if not any(getattr(tool, "name", "") == PRESENT_FILE_TOOL_NAME for tool in tools):
        return ""
    if relay is None:
        raise RuntimeError(
            "present_file is offered but the daemon's present-file relay is not running; "
            "it starts with the flock mesh (mesh.enabled with cascade.enabled)"
        )
    return relay.socket_path


def _notify_tool_allowed(settings: Any, persona: Any | None) -> bool:
    """Whether the resident's ``notify`` tool is offered (see ``ResidentNotifyTool``)."""
    from ravn.adapters.tools.notify import NOTIFY_TOOL_NAME  # noqa: PLC0415

    if NOTIFY_TOOL_NAME in set(settings.tools.disabled):
        return False
    forbidden = set(getattr(persona, "forbidden_tools", None) or [])
    return NOTIFY_TOOL_NAME not in forbidden
