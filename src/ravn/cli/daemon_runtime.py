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

    _build_learned_tool_resolver(settings, workspace)
    cli_transport_executor = _uses_cli_transport_executor(persona_config)
    llm = None if cli_transport_executor else _build_llm(settings)
    memory = _build_memory(settings)
    compressor = None if cli_transport_executor else _build_compressor(settings, llm)
    pre_hooks, post_hooks = _build_hooks(settings)

    mcp_manager: Any | None = None
    mcp_tools: list[Any] = []

    # Build Mímir adapter early so _agent_factory closure can capture it.
    daemon_mimir = _build_mimir(settings)
    drive_loop: Any | None = None
    resident_inbox: Any | None = None
    resident_state: Any | None = None
    resident_runtime: Any | None = None
    if settings.initiative.enabled or task_dispatch:
        resident_inbox = _build_resident_inbox(
            settings,
            workspace=workspace,
            mimir=daemon_mimir,
        )
        resident_state = await _build_resident_state(
            settings,
            workspace=workspace,
            mimir=daemon_mimir,
        )
        resident_runtime = _build_resident_runtime(
            settings,
            state=resident_state,
            inbox=resident_inbox,
        )

    # Populated by _wire_cron after drive_loop is created; captured by _agent_factory.
    cron_tools: list[Any] = []

    # NIU-598: shared in-process bus for post-session reflection (daemon mode).
    # Captured by _agent_factory so each agent created by the daemon publishes to it.
    daemon_bus: Any | None = None
    sleipnir_catalog_publisher: Any | None = None
    if settings.reflection.enabled:
        from sleipnir.adapters.in_process import InProcessBus

        daemon_bus = InProcessBus()
        if settings.observability.enabled:
            from ravn.adapters.observability import ObservedSleipnirBus  # noqa: PLC0415

            daemon_bus = ObservedSleipnirBus(daemon_bus)

    # This publisher is shared by gateways, the drive loop, and the resident
    # learning services. Start it before constructing any of those producers so
    # an immediately-ready trigger cannot publish against an unstarted adapter.
    environment_signal_publisher_started = False
    environment_signal_publisher: Any | None = _build_environment_signal_publisher(settings)
    if environment_signal_publisher is not None and hasattr(
        environment_signal_publisher,
        "start",
    ):
        await environment_signal_publisher.start()
        environment_signal_publisher_started = True

    resident_learning_runtime = _build_resident_learning_runtime(
        settings,
        publisher=environment_signal_publisher,
        workspace=workspace,
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

        async def _emit_a2a_activity(activity: dict[str, object]) -> None:
            if drive_loop is None:
                return
            current_task = drive_loop.resolve_task_context(task_id)
            if current_task is None:
                logger.warning("a2a_activity: no task context available for task_id=%s", task_id)
                return
            durable_activity = {
                **activity,
                "case_id": current_task.resident_case_id,
                "root_correlation_id": current_task.root_correlation_id,
                "parent_task_id": current_task.task_id,
                "mandate": current_task.resident_mandate or current_task.initiative_context,
                "turn_index": current_task.resident_turn_index,
                "case_input_tokens": current_task.resident_input_tokens,
                "case_output_tokens": current_task.resident_output_tokens,
                "case_started_at": current_task.resident_started_at,
            }
            if resident_runtime is not None:
                await resident_runtime.record_a2a_activity(durable_activity)
            drive_loop.record_a2a_activity(
                parent_task_id=current_task.task_id,
                activity=durable_activity,
            )

        async def _find_a2a_activity(
            *,
            query: str = "",
            active_only: bool = False,
            limit: int = 10,
        ) -> list[dict[str, object]]:
            if resident_runtime is None:
                return []
            current_task = drive_loop.resolve_task_context(task_id) if drive_loop else None
            case_id = "" if query.strip() else getattr(current_task, "resident_case_id", "")
            return await resident_runtime.find_a2a_tasks(
                query=query,
                case_id=case_id,
                active_only=active_only,
                limit=limit,
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
            a2a_activity_emitter=_emit_a2a_activity,
            a2a_activity_finder=_find_a2a_activity,
            skill_manager=(
                resident_learning_runtime.skills if resident_learning_runtime is not None else None
            ),
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

        # NIU-571: Apply trust gradient constraints for thread-triggered tasks
        tools = _apply_trust_filter(tools, settings, triggered_by)

        executor = _build_executor(resolved_persona)
        resolved_extended_thinking = _resolve_extended_thinking(
            settings,
            resolved_persona,
            cli_transport_executor=_uses_cli_transport_executor(resolved_persona),
        )
        agent = executor.build(
            llm=llm,
            tools=tools,
            channel=channel,
            permission=permission,
            system_prompt=resolved_system_prompt,
            model=resolved_model,
            max_tokens=resolved_max_tokens,
            max_iterations=resolved_max_iterations,
            workspace_dir=str(workspace),
            mcp_servers=_transport_mcp_servers(settings),
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
            prompt_builder=_build_prompt_builder(settings),
            reflection_model=settings.effective_memory_reflection_model(),
            reflection_max_tokens=settings.memory.reflection_max_tokens,
            task_summary_max_chars=settings.memory.task_summary_max_chars,
            input_token_cost_per_million=settings.memory.input_token_cost_per_million,
            output_token_cost_per_million=settings.memory.output_token_cost_per_million,
            extended_thinking=resolved_extended_thinking,
            # NIU-598: session lifecycle events for optional reflection storage
            sleipnir_publisher=daemon_bus,
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
        )
        # Reviews and flock proposals must cross the mesh, not stay on the
        # daemon's in-process reflection bus — same precedence as the drive
        # loop's sleipnir publisher (closure binds late; the signal publisher
        # is built during daemon boot, before any task runs).
        allowed_tools = _expand_allowed_tools(
            set(resolved_persona.allowed_tools or []) if resolved_persona else set()
        )
        return _attach_agent_build_tool(
            agent,
            workspace,
            enabled="build_tool" in allowed_tools,
            settings=settings,
            publisher=environment_signal_publisher or sleipnir_catalog_publisher or daemon_bus,
            drive_loop=drive_loop,
            a2a_activity_emitter=_emit_a2a_activity,
            installed_artifact_recorder=(
                resident_learning_runtime.register_installed_artifact
                if resident_learning_runtime is not None
                else None
            ),
        )

    tasks: list[asyncio.Task] = []

    # Create interaction tracker early so it can be shared between gateway
    # channels (touch on operator message) and the wakefulness trigger (read).
    from ravn.domain.interaction_tracker import LastInteractionTracker

    interaction_tracker = LastInteractionTracker()

    # Gateway channels (human-initiated turns)
    gw_tasks: list[str] = []
    http_gateway: Any | None = None
    channels_cfg = settings.gateway.channels
    _any_channel = (
        channels_cfg.telegram.enabled
        or channels_cfg.http.enabled
        or channels_cfg.discord.enabled
        or channels_cfg.slack.enabled
        or channels_cfg.matrix.enabled
        or channels_cfg.whatsapp.enabled
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
            http_gateway = HttpGateway(
                channels_cfg.http,
                gw,
                resident_runtime=resident_runtime,
            )
            tasks.append(asyncio.create_task(http_gateway.run(), name="http"))
            gw_tasks.append("http")

        for task, name in _make_channel_tasks(channels_cfg, gw):
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
    environment_signal_runtime: Any | None = None
    resident_wakefulness: Any | None = None
    odin_court: Any | None = None
    feedback_recorder: Any | None = None
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
                        "mimir.dream.completed emission will be skipped: %s",
                        exc,
                    )
                    sleipnir_catalog_publisher = None
            else:
                logger.warning(
                    "sleipnir: %s is not set; catalog events will not be published",
                    settings.sleipnir.amqp_url_env,
                )

        drive_loop = DriveLoop(
            agent_factory=_agent_factory,
            config=settings.initiative,
            settings=settings,
            event_publisher=event_publisher,
            resume=resume,
            mimir=daemon_mimir,
            sleipnir_publisher=environment_signal_publisher
            or sleipnir_catalog_publisher
            or daemon_bus,
        )
        if hasattr(drive_loop, "set_persona_config"):
            drive_loop.set_persona_config(persona_config)
        if resident_runtime is not None:
            from ravn.resident_runtime import ResidentHomeTrigger  # noqa: PLC0415

            if hasattr(drive_loop, "set_resident_runtime"):
                drive_loop.set_resident_runtime(resident_runtime)
            if http_gateway is not None:
                http_gateway.bind_resident_status_provider(drive_loop.resident_hud_status)
            if hasattr(drive_loop, "register_directed_message_interceptor"):
                drive_loop.register_directed_message_interceptor(
                    resident_runtime.consume_directed_message
                )
            if resident_inbox is not None and hasattr(drive_loop, "register_trigger"):
                try:
                    resident_output_mode = OutputMode(settings.initiative.default_output_mode)
                except ValueError:
                    resident_output_mode = OutputMode.AMBIENT
                drive_loop.register_trigger(
                    ResidentHomeTrigger(
                        resident_runtime,
                        interval_seconds=settings.resident_state.home_wake_interval_seconds,
                        max_signals=settings.resident_inbox.max_signals_per_wake,
                        persona=(
                            persona_config.name
                            if persona_config is not None
                            else settings.initiative.default_persona or None
                        ),
                        output_mode=resident_output_mode,
                    )
                )
        _cron_jobs = _wire_triggers(drive_loop, settings.initiative)
        cron_tools[:] = _wire_cron(drive_loop, _cron_jobs, settings.initiative)

        # Wire Mímir triggers (source synthesis + staleness refresh + threads)
        if daemon_mimir is not None:
            _wire_mimir_triggers(
                drive_loop,
                daemon_mimir,
                settings,
                llm=llm,
                interaction_tracker=interaction_tracker,
                agent_factory=_agent_factory,
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

        trigger_names = [t.name for t in drive_loop._triggers]
        tasks.append(asyncio.create_task(drive_loop.run(), name="drive_loop"))

    if resident_learning_runtime is not None:
        await resident_learning_runtime.start()
        tasks.append(asyncio.create_task(asyncio.Event().wait(), name="resident_learning"))
        typer.echo("  Resident learning: subscribed")

    realm_capability_sync = _build_realm_capability_sync(
        settings,
        subscriber=environment_signal_publisher,
    )
    if realm_capability_sync is not None:
        await realm_capability_sync.start()
        typer.echo("  Realm capability sync: subscribed")

    resident_wakefulness = _build_resident_wakefulness(
        settings,
        resident_learning_runtime=resident_learning_runtime,
        publisher=environment_signal_publisher,
        memory=memory,
    )
    if resident_wakefulness is not None:
        await resident_wakefulness.start()
        typer.echo("  Resident wakefulness: started")

    odin_court = _build_odin_court(
        settings,
        publisher=environment_signal_publisher,
        memory=memory,
        review_requester=(
            resident_learning_runtime.review_requester
            if resident_learning_runtime is not None
            else None
        ),
    )
    if odin_court is not None:
        await odin_court.start()
        tasks.append(
            asyncio.create_task(
                _run_odin_court_sweep(
                    odin_court,
                    settings.odin_court.sweep_interval_seconds,
                ),
                name="odin_court_sweep",
            )
        )
        typer.echo("  ODIN court: subscribed")

    feedback_recorder = _build_feedback_recorder(
        settings,
        publisher=environment_signal_publisher,
        memory=memory,
    )
    if feedback_recorder is not None:
        await feedback_recorder.start()
        typer.echo("  Feedback recorder: subscribed")

    if (
        daemon_mimir is not None
        and environment_signal_publisher is not None
        and hasattr(environment_signal_publisher, "subscribe")
        and (settings.environment.flocks or settings.environment.signal_sources)
    ):
        from ravn.huddle_archiver import HuddleTranscriptArchiver  # noqa: PLC0415

        huddle_archiver = HuddleTranscriptArchiver(
            subscriber=environment_signal_publisher,
            mimir=daemon_mimir,
        )
        await huddle_archiver.start()
        typer.echo("  Huddle transcript archiver: subscribed")

    environment_signal_runtime = _build_environment_signal_runtime(
        settings,
        drive_loop=drive_loop,
        persona_config=persona_config,
        publisher=environment_signal_publisher,
        mimir=daemon_mimir,
        resident_learning_runtime=resident_learning_runtime,
        resident_wakefulness=resident_wakefulness,
        resident_inbox=resident_inbox,
        owns_publisher=not environment_signal_publisher_started,
    )
    if environment_signal_runtime is not None:
        await environment_signal_runtime.start()
        tasks.append(
            asyncio.create_task(environment_signal_runtime.wait(), name="environment_signals")
        )

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

    # NIU-598: start post-session reflection service for daemon mode.
    daemon_reflection_svc: Any | None = None
    if daemon_bus is not None and daemon_mimir is not None:
        from ravn.adapters.reflection.post_session import PostSessionReflectionService

        daemon_reflection_svc = PostSessionReflectionService(
            subscriber=daemon_bus,
            mimir=daemon_mimir,
            llm=llm,
            config=settings.effective_post_session_reflection_config(),
        )
        await daemon_reflection_svc.start()

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
        typer.echo("No channels or triggers enabled — daemon has nothing to do.", err=True)
        if daemon_bus is not None:
            with suppress(Exception):
                await daemon_bus.flush()
        if daemon_reflection_svc is not None:
            await daemon_reflection_svc.stop()
        if sleipnir_catalog_publisher is not None:
            await sleipnir_catalog_publisher.stop()
        if environment_signal_runtime is not None:
            await environment_signal_runtime.stop()
        if resident_wakefulness is not None:
            await resident_wakefulness.stop()
        if odin_court is not None:
            await odin_court.stop()
        if feedback_recorder is not None:
            await feedback_recorder.stop()
        if huddle_archiver is not None:
            await huddle_archiver.stop()
        if resident_learning_runtime is not None:
            await resident_learning_runtime.stop()
        if environment_signal_publisher_started and environment_signal_publisher is not None:
            await environment_signal_publisher.stop()
        shutdown_observability()
        return

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
        if _cascade_participant is not None:
            await _cascade_participant.stop()
        if resident_wakefulness is not None:
            await resident_wakefulness.stop()
        if odin_court is not None:
            await odin_court.stop()
        if feedback_recorder is not None:
            await feedback_recorder.stop()
        if huddle_archiver is not None:
            await huddle_archiver.stop()
        if resident_learning_runtime is not None:
            await resident_learning_runtime.stop()
        if environment_signal_runtime is not None:
            await environment_signal_runtime.stop()
        if environment_signal_publisher_started and environment_signal_publisher is not None:
            await environment_signal_publisher.stop()
        await event_publisher.close()
        await _shutdown_mcp(mcp_manager)
        # NIU-598: flush pending events before tearing down daemon reflection service.
        if daemon_bus is not None:
            with suppress(Exception):
                await daemon_bus.flush()
        if daemon_reflection_svc is not None:
            await daemon_reflection_svc.stop()
        if sleipnir_catalog_publisher is not None:
            await sleipnir_catalog_publisher.stop()
        shutdown_observability()
