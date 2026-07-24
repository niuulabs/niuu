"""Focused runtime implementation extracted from :mod:`ravn.cli.commands`."""

from __future__ import annotations

import uuid

from ravn.domain.events import RavnEvent, RavnEventType

# Dependencies are supplied by the CLI compatibility facade immediately before
# invocation so established command patch points remain effective.
# ruff: noqa: F821


def _wire_mimir_triggers(
    drive_loop: Any,
    mimir: Any,
    settings: Settings,
    llm: Any = None,
    interaction_tracker: Any = None,
    agent_factory: Any | None = None,
) -> None:
    """Register Mímir source, staleness, and thread triggers on the drive loop.

    All triggers are gated by their individual ``enabled`` flags in
    ``settings.mimir.source_trigger``, ``settings.mimir.staleness_trigger``,
    and ``settings.thread``.
    """
    mc = settings.mimir
    workflow_runtime = bool(settings.workflow.graph)

    if workflow_runtime:
        logger.info(
            "mimir: workflow runtime detected — background source/staleness triggers disabled"
        )
    elif mc.source_trigger.enabled:
        from ravn.adapters.triggers.mimir_source import MimirSourceTrigger

        drive_loop.register_trigger(MimirSourceTrigger(mimir=mimir, config=mc.source_trigger))
        logger.info(
            "mimir: source trigger registered (poll=%ds, persona=%s)",
            mc.source_trigger.poll_interval_seconds,
            mc.source_trigger.persona,
        )

    if not workflow_runtime and mc.staleness_trigger.enabled:
        from ravn.adapters.mimir.usage_log import LogBasedUsageAdapter
        from ravn.adapters.triggers.mimir_staleness import MimirStalenessTrigger

        usage = LogBasedUsageAdapter(mimir_root=mc.path)
        drive_loop.register_trigger(
            MimirStalenessTrigger(mimir=mimir, usage=usage, config=mc.staleness_trigger)
        )
        logger.info(
            "mimir: staleness trigger registered (schedule=%dh, top_n=%d, persona=%s)",
            mc.staleness_trigger.schedule_hours,
            mc.staleness_trigger.top_n,
            mc.staleness_trigger.persona,
        )

    # Thread queue trigger — wired always; only fires when thread.enabled=True.
    from ravn.adapters.triggers.thread_queue import ThreadQueueTrigger

    drive_loop.register_trigger(ThreadQueueTrigger(mimir=mimir, config=settings.thread))
    logger.info(
        "thread: queue trigger registered (enabled=%s, poll_interval=%ds)",
        settings.thread.enabled,
        settings.thread.enricher_poll_interval_seconds,
    )

    # Thread enricher (Sjón) — classifies new Mímir pages as threads.
    if settings.thread.enabled and llm is not None:
        if _uses_cli_transport_runtime():
            logger.info(
                "thread: enricher skipped for CLI-transport runtime; auxiliary LLM hooks still "
                "need transport-backed support"
            )
        else:
            from ravn.adapters.triggers.thread_enricher import ThreadEnricher

            drive_loop.register_trigger(
                ThreadEnricher(mimir=mimir, llm=llm, config=settings.thread)
            )
            logger.info(
                "thread: enricher registered (poll=%ds, confidence=%.2f, llm_alias=%s)",
                settings.thread.enricher_poll_interval_seconds,
                settings.thread.confidence_threshold,
                settings.thread.enricher_llm_alias,
            )

    # Wakefulness trigger (NIU-565) — detects silence, reflects, emits intents.
    if settings.resident_wakefulness.enabled and llm is not None:
        if _uses_cli_transport_runtime():
            logger.info(
                "wakefulness: skipped for CLI-transport runtime; auxiliary LLM hooks still need "
                "transport-backed support"
            )
        elif interaction_tracker is None:
            logger.warning("wakefulness: no interaction tracker provided — skipping")
        else:
            from ravn.adapters.triggers.wakefulness import WakefulnessTrigger

            drive_loop.register_trigger(
                WakefulnessTrigger(
                    tracker=interaction_tracker,
                    mimir=mimir,
                    llm=llm,
                    config=settings.wakefulness,
                )
            )
            logger.info(
                "wakefulness: trigger registered (silence=%ds, cooldown=%ds, poll=%ds)",
                settings.wakefulness.silence_threshold_seconds,
                settings.wakefulness.reflection_cooldown_seconds,
                settings.wakefulness.poll_interval_seconds,
            )

    # Recap trigger (NIU-569) — surfaces overnight work on operator return.
    if settings.recap.enabled:
        if interaction_tracker is None:
            logger.warning("recap: no interaction tracker provided — skipping")
        else:
            from ravn.adapters.triggers.recap import RecapTrigger

            drive_loop.register_trigger(
                RecapTrigger(
                    mimir=mimir,
                    config=settings.recap,
                    last_interaction=interaction_tracker.last,
                )
            )
            logger.info(
                "recap: trigger registered (absence=%ds, window=%ds, cron=%r, poll=%ds)",
                settings.recap.absence_threshold_seconds,
                settings.recap.return_detection_window_seconds,
                settings.recap.scheduled_recap_cron,
                settings.recap.poll_interval_seconds,
            )

    # Dream cycle trigger (NIU-587) — nightly Mímir enrichment, lint, cross-reference.
    if settings.dream_cycle.enabled:
        from ravn.adapters.triggers.dream_cycle import DreamCycleTrigger

        drive_loop.register_trigger(DreamCycleTrigger(config=settings.dream_cycle))
        logger.info(
            "dream_cycle: trigger registered (cron=%r, persona=%r, budget=$%.2f, poll=%ds)",
            settings.dream_cycle.cron_expression,
            settings.dream_cycle.persona,
            settings.dream_cycle.token_budget_usd,
            settings.dream_cycle.poll_interval_seconds,
        )


def _wire_cron(
    drive_loop: Any,
    cron_jobs: list[Any],
    initiative: InitiativeConfig,
) -> list[Any]:
    """Create a single CronTrigger + CronJobStore and wire cron tools (NIU-437).

    A single CronTrigger is always registered so runtime jobs created via
    ``cron_create`` are serviced even when no config-defined cron triggers exist.
    The store is backed by ``~/.ravn/cron/jobs.json`` (0600).

    Returns the list of cron tool instances for the caller to pass to the agent
    factory — avoids monkey-patching drive_loop with private attributes.
    """
    from ravn.adapters.tools.cron_tools import build_cron_tools
    from ravn.adapters.triggers.cron import make_cron_trigger

    journal_dir = Path(initiative.queue_journal_path).expanduser().parent
    trigger, store = make_cron_trigger(
        jobs=cron_jobs,
        jobs_path=journal_dir / "cron_jobs.json",
        state_path=journal_dir / "cron_state.json",
        lock_path=journal_dir / "cron.lock",
        tick_seconds=initiative.cron_tick_seconds,
    )
    drive_loop.register_trigger(trigger)
    tools = build_cron_tools(store)
    logger.info(
        "cron: wired %d config job(s); store at %s",
        len(cron_jobs),
        store._path,
    )
    return tools


def _wire_task_dispatch(drive_loop: Any, sleipnir_config: Any) -> None:
    """Register a TaskDispatchChannel as a drive-loop trigger (NIU-505)."""
    from ravn.adapters.channels.event import TaskDispatchChannel

    channel = TaskDispatchChannel(sleipnir_config)
    drive_loop.register_trigger(channel)
    logger.info("task_dispatch: registered ravn.task.dispatch subscription")


def _derive_capabilities(
    settings: Settings,
    persona_config: Any | None = None,
    profile_name: str = "default",
) -> list[str]:
    """Derive the capability strings advertised in the mesh peer identity.

    If the active persona has an explicit ``allowed_tools`` list, those group
    names are used directly (minus any ``forbidden_tools``).  Otherwise the
    profile's ``include_groups`` are used so that peers without a persona still
    advertise a meaningful capability set.
    """
    if persona_config is not None:
        allowed = list(getattr(persona_config, "allowed_tools", None) or [])
        if allowed:
            forbidden = set(getattr(persona_config, "forbidden_tools", None) or [])
            return [c for c in allowed if c not in forbidden]

    profile_cfg = _get_tool_group(settings, profile_name)
    return list(profile_cfg.include_groups)


def _wire_cascade(
    drive_loop: Any,
    settings: Settings,
    persona_config: Any | None = None,
    profile_name: str = "default",
) -> Any:
    """Wire cascade tools and mesh RPC handler onto the drive loop.

    This wires up:
    - The mesh RPC handler (task_dispatch, task_status, task_cancel)
    - Cascade tools are registered later when the agent factory is called

    The drive_loop is mutated in-place (set_rpc_handler).
    """
    from ravn.adapters.tools.cascade_tools import build_cascade_tools  # noqa: PLC0415

    # Build optional mesh and discovery adapters (discovery first — mesh needs it)
    mesh: Any = None
    discovery: Any = None

    if settings.discovery.enabled:
        try:
            discovery = _build_discovery(settings, persona_config, profile_name)
        except Exception as exc:
            logger.warning("cascade: failed to build discovery adapter: %s", exc)

    if settings.mesh.enabled:
        try:
            mesh = _build_mesh(settings, discovery)
        except Exception as exc:
            logger.warning("cascade: failed to build mesh adapter: %s", exc)

    # Build cascade tools (Mode 1 always; Mode 2/3 when mesh/discovery available)
    allowed_target_personas = None
    if persona_config is not None:
        allowed_target_personas = _workflow_allowed_task_targets(settings, persona_config.name)
        if allowed_target_personas is not None:
            logger.info(
                "cascade: workflow graph restricts %s task_create targets to %s",
                persona_config.name,
                sorted(allowed_target_personas),
            )

    def _resolve_allowed_targets() -> set[str] | None:
        if persona_config is None:
            return None
        current_task = drive_loop.current_task() if hasattr(drive_loop, "current_task") else None
        current_node_id = (
            str(getattr(current_task, "workflow_node_id", "") or "").strip() if current_task else ""
        )
        if current_node_id:
            return (
                _workflow_allowed_task_targets(
                    settings,
                    persona_config.name,
                    node_id=current_node_id,
                )
                or set()
            )
        return _workflow_allowed_task_targets(settings, persona_config.name)

    cascade_tools = build_cascade_tools(
        drive_loop=drive_loop,
        mesh=mesh,
        discovery=discovery,
        spawn_adapter=None,  # spawn adapter wired separately if needed
        cascade_config=settings.cascade,
        allowed_target_personas=allowed_target_personas,
        allowed_target_resolver=_resolve_allowed_targets,
    )
    logger.info(
        "cascade: registered %d tools (mesh=%s, discovery=%s)",
        len(cascade_tools),
        mesh is not None,
        discovery is not None,
    )

    # Build mesh routing tools (event-type based routing)
    from ravn.adapters.tools.mesh_routing_tools import build_mesh_routing_tools  # noqa: PLC0415

    mesh_routing_tools = build_mesh_routing_tools(mesh=mesh, discovery=discovery)
    if mesh_routing_tools:
        logger.info("mesh_routing: registered %d tools", len(mesh_routing_tools))

    # Store all tools on drive_loop for agent_factory to pick up
    drive_loop._cascade_tools = cascade_tools + mesh_routing_tools

    # Wire the mesh RPC handler
    async def _handle_mesh_rpc(message: dict) -> dict:
        msg_type = message.get("type")

        if msg_type == "task_dispatch":
            task_dict = message.get("task", {})
            try:
                task = AgentTask(
                    task_id=task_dict["task_id"],
                    title=task_dict.get("title", "remote task"),
                    initiative_context=task_dict.get("initiative_context", ""),
                    triggered_by=task_dict.get("triggered_by", "cascade:remote"),
                    output_mode=OutputMode(task_dict.get("output_mode", "silent")),
                    persona=task_dict.get("persona"),
                    priority=int(task_dict.get("priority", 5)),
                )
                if task_dict.get("session_id"):
                    task.session_id = str(task_dict["session_id"])
                if task_dict.get("root_correlation_id"):
                    task.root_correlation_id = str(task_dict["root_correlation_id"])
                await drive_loop.enqueue(task)
                return {"status": "accepted", "task_id": task.task_id}
            except Exception as exc:
                logger.error("cascade: task_dispatch failed: %s", exc)
                return {"status": "rejected", "error": str(exc)}

        if msg_type == "task_list":
            return {
                "active": drive_loop.active_task_ids(),
                "queued": drive_loop.queued_task_ids(),
            }

        if msg_type == "task_status":
            task_id = message.get("task_id", "")
            include_progress = bool(message.get("include_progress", False))
            status_result = drive_loop.task_status(task_id, include_progress=include_progress)
            if include_progress and isinstance(status_result, dict):
                return {"task_id": task_id, **status_result}
            return {"task_id": task_id, "status": status_result}

        if msg_type == "task_cancel":
            task_id = message.get("task_id", "")
            await drive_loop.cancel(task_id)
            return {"status": "cancelled", "task_id": task_id}

        if msg_type == "directed_message":
            content = str(message.get("content") or "")
            metadata = message.get("metadata")
            if not content.strip():
                return {"status": "rejected", "error": "empty content"}
            if not isinstance(metadata, dict):
                metadata = {}
            accepted = await drive_loop.handle_directed_message(content, metadata)
            return {"status": "accepted" if accepted else "rejected"}

        if msg_type == "task_result":
            task_id = message.get("task_id", "")
            result = drive_loop.get_result(task_id)
            if result is None:
                return {"error": "task_result_not_found", "task_id": task_id}
            return {
                "task_id": task_id,
                "status": result.status,
                "output": result.output,
                "event_count": len(result.events),
            }

        if msg_type == "work_request":
            # Synchronous work request - enqueue, wait for completion, return result
            # Used for event-type based routing (persona-to-persona work delegation)
            prompt = message.get("prompt", "")
            event_type = message.get("event_type", "")
            request_id = message.get("request_id", str(uuid.uuid4()))
            timeout_s = float(message.get("timeout_s", 120.0))

            task = AgentTask(
                task_id=f"work_{request_id}",
                title=f"Work request: {event_type}" if event_type else "Work request",
                initiative_context=prompt,
                triggered_by=f"mesh:work_request:{event_type}",
                output_mode=OutputMode.SILENT,
                priority=5,
            )
            if message.get("session_id"):
                task.session_id = str(message["session_id"])
            if message.get("root_correlation_id"):
                task.root_correlation_id = str(message["root_correlation_id"])

            try:
                await drive_loop.enqueue(task)
                # Wait for completion with timeout
                result = await asyncio.wait_for(
                    drive_loop.wait_for_result(task.task_id),
                    timeout=timeout_s,
                )
                output = result.output if result else ""

                # Parse outcome block if present
                from niuu.domain.outcome import parse_outcome_block  # noqa: PLC0415

                response: dict[str, Any] = {
                    "status": "complete",
                    "request_id": request_id,
                    "output": output,
                    "event_type": event_type,
                }

                parsed = parse_outcome_block(output)
                if parsed is not None:
                    response["outcome"] = {
                        "fields": parsed.fields,
                        "valid": parsed.valid,
                        "errors": parsed.errors,
                    }

                return response
            except TimeoutError:
                return {"status": "timeout", "request_id": request_id, "event_type": event_type}
            except Exception as exc:
                logger.error("work_request failed: %s", exc)
                return {"status": "error", "request_id": request_id, "error": str(exc)}

        return {"error": "unknown_message_type", "type": msg_type}

    drive_loop.set_rpc_handler(_handle_mesh_rpc)

    if mesh is not None and hasattr(mesh, "set_rpc_handler"):
        mesh.set_rpc_handler(drive_loop.handle_rpc)

    # Wire mesh and persona_config for outcome event publishing
    drive_loop.set_mesh(mesh)
    drive_loop.set_persona_config(persona_config)
    drive_loop.set_workflow_allowed_outcomes_resolver(
        lambda task, _persona: _workflow_allowed_outcome_topics(
            settings,
            node_id=str(getattr(task, "workflow_node_id", "") or "").strip() or None,
        )
    )

    # Subscribe to event types this persona consumes
    if mesh is not None and persona_config is not None:
        from niuu.mesh import resolve_peer_id  # noqa: PLC0415
        from ravn.workflow_kickoff import WorkflowKickoffAcknowledger  # noqa: PLC0415

        kickoff_acknowledger = WorkflowKickoffAcknowledger(
            mesh=mesh,
            peer_id=resolve_peer_id(settings.mesh.own_peer_id),
            persona=persona_config.name,
        )
        consumes = getattr(persona_config, "consumes", None)
        event_types = list(getattr(consumes, "event_types", []) if consumes else [])
        workflow_consumer_groups: list[dict[str, Any]] = []
        workflow_runtime = _workflow_runtime_for_persona(settings, persona_config.name)
        if workflow_runtime is not None and workflow_runtime["event_types"]:
            event_types = list(workflow_runtime["event_types"])
            workflow_consumer_groups = list(workflow_runtime.get("consumer_groups") or [])
            logger.info(
                "mesh: workflow graph overrides consumed event_types for %s: %s",
                persona_config.name,
                event_types,
            )

        # Register fan-in contributors from the persona catalog so the
        # buffer knows how many producer outcomes to collect.
        # When discovery is active, only include personas that are actual
        # peers in the flock — not all installed personas.
        if persona_config and persona_config.fan_in.contributes_to:
            from ravn.adapters.personas.loader import FilesystemPersonaAdapter  # noqa: PLC0415

            loader = FilesystemPersonaAdapter()
            target = persona_config.fan_in.contributes_to
            contributors = loader.find_contributors(target)

            # Filter to only peers present in the flock (via discovery)
            if discovery is not None and hasattr(discovery, "peers"):
                flock_personas = {p.persona for p in discovery.peers().values()}
                # Include self — discovery.peers() only returns others
                if persona_config:
                    flock_personas.add(persona_config.name)
                contributors = [c for c in contributors if c.name in flock_personas]

            # Only enable fan-in when there are multiple contributors to wait for.
            # Solo contributor (e.g. reviewer without security in the flock)
            # acts independently — no fan-in accumulation needed.
            if len(contributors) > 1:
                drive_loop.fan_in.set_contributors(target, [c.name for c in contributors])
                logger.info(
                    "mesh: fan-in contributors for %s: %s",
                    target,
                    [c.name for c in contributors],
                )
            else:
                logger.info(
                    "mesh: solo contributor for %s — fan-in disabled",
                    target,
                )

        fan_in_strategy = (
            str(workflow_runtime["fan_in_strategy"])
            if workflow_runtime is not None
            else persona_config.fan_in.strategy
        )
        fan_in_contributes_to = persona_config.fan_in.contributes_to if persona_config else ""

        async def _handle_outcome_event(event: RavnEvent) -> None:
            """Handle incoming outcome events, respecting fan-in accumulation."""
            if event.type != RavnEventType.OUTCOME:
                return

            payload = event.payload
            event_type = payload.get("event_type", "")
            source_persona = payload.get("persona", "")
            source_task_id = event.task_id or event.correlation_id
            root_corr = event.root_correlation_id or event.correlation_id
            source_event_id = event.event_id or source_task_id
            cycle_corr = str(payload.get("workflow_parent_event_id") or root_corr)

            logger.info(
                "mesh: received outcome event_type=%s from=%s task_id=%s root=%s",
                event_type,
                source_persona,
                source_task_id,
                root_corr,
            )

            # --- Workflow kickoff handshake ---
            # Ack the kickoff before any LLM work so Skuld stops redelivering;
            # a redelivery means our previous ack was lost, so re-ack it but
            # never enqueue the same kickoff twice.
            if kickoff_acknowledger.is_kickoff(event):
                first_delivery = await kickoff_acknowledger.acknowledge(event)
                if not first_delivery:
                    logger.info(
                        "mesh: ignoring redelivered workflow kickoff event_type=%s root=%s",
                        event_type,
                        root_corr,
                    )
                    return

            # --- Producer aggregation ---
            # If the source persona contributes_to a target, check if all
            # contributors have reported before proceeding.
            if fan_in_contributes_to:
                agg_result = drive_loop.fan_in.try_accept_producer(
                    contributes_to=fan_in_contributes_to,
                    producer_persona=source_persona,
                    event_type=event_type,
                    event_payload=payload,
                    root_correlation_id=root_corr,
                    cycle_correlation_id=cycle_corr,
                )
                if agg_result is not None:
                    logger.info(
                        "mesh: producer fan-in complete for %s",
                        fan_in_contributes_to,
                    )
                    # Producer aggregation result is informational — the actual
                    # task dispatch happens via consumer accumulation below.

            # --- Consumer accumulation ---
            consumer_groups = workflow_consumer_groups or [
                {
                    "id": persona_config.name,
                    "label": persona_config.name,
                    "event_types": list(event_types),
                    "fan_in_strategy": fan_in_strategy,
                }
            ]
            pending_groups: list[str] = []
            matched = False
            for group in consumer_groups:
                group_event_types = list(group.get("event_types") or [])
                if event_type not in group_event_types:
                    continue
                group_filters = group.get("event_filters") or {}
                if isinstance(group_filters, dict) and group_filters:
                    normalized_filters = {
                        str(key): str(value)
                        for key, value in group_filters.items()
                        if str(key).strip() and str(value).strip()
                    }
                    if normalized_filters and not _workflow_event_matches_filters(
                        payload,
                        normalized_filters,
                    ):
                        logger.debug(
                            "mesh: filtered outcome event_type=%s for %s group=%s filters=%s",
                            event_type,
                            persona_config.name if persona_config else "unknown",
                            group.get("id") or persona_config.name,
                            normalized_filters,
                        )
                        continue
                matched = True
                group_id = str(group.get("id") or persona_config.name)
                group_strategy = str(group.get("fan_in_strategy") or fan_in_strategy)
                result = drive_loop.fan_in.try_accept_consumer(
                    event_type=event_type,
                    event_payload=payload,
                    root_correlation_id=root_corr,
                    persona_name=persona_config.name if persona_config else "unknown",
                    consumes_event_types=group_event_types,
                    strategy=group_strategy,
                    consumer_key=group_id,
                    cycle_correlation_id=cycle_corr,
                )

                if result is None:
                    pending_groups.append(group_id)
                    continue

                task_id_suffix = (root_corr or "unknown")[:8]
                safe_group_id = group_id.replace(".", "_").replace("-", "_")
                stage_context = _workflow_stage_context(settings, node_id=group_id)
                initiative_context = (
                    f"{stage_context}\n\n{result.merged_context}"
                    if stage_context
                    else result.merged_context
                )
                task = AgentTask(
                    task_id=f"event_{safe_group_id}_{task_id_suffix}",
                    title=f"Handle {result.triggered_by}",
                    initiative_context=initiative_context,
                    triggered_by=result.triggered_by,
                    output_mode=OutputMode.SILENT,
                    persona=(
                        result.persona_name if result.persona_name != persona_config.name else None
                    ),
                    priority=5,
                    root_correlation_id=result.root_correlation_id,
                    workflow_parent_event_id=source_event_id,
                    workflow_node_id=group_id,
                )
                task.session_id = event.session_id or task.session_id
                task.trace_context = dict(event.trace_context)

                try:
                    accepted = await drive_loop.enqueue(task)
                    if accepted:
                        logger.info(
                            "mesh: enqueued task %s for %s (fan-in: %s group=%s)",
                            task.task_id,
                            result.triggered_by,
                            group_strategy,
                            group_id,
                        )
                    else:
                        logger.info(
                            "mesh: task %s for %s was already queued or active",
                            task.task_id,
                            result.triggered_by,
                        )
                except Exception as exc:
                    logger.error("mesh: failed to enqueue task for event: %s", exc)

            if pending_groups:
                logger.info(
                    "mesh: fan-in pending for %s groups=%s — waiting for more events",
                    persona_config.name if persona_config else "unknown",
                    pending_groups,
                )
            elif not matched:
                logger.debug(
                    "mesh: ignoring outcome event_type=%s for %s",
                    event_type,
                    persona_config.name if persona_config else "unknown",
                )

        # Store pending subscriptions - will be activated after mesh.start()
        mesh._pending_outcome_subscriptions = [(et, _handle_outcome_event) for et in event_types]
        for event_type in event_types:
            logger.info("mesh: will subscribe to event_type=%s after start", event_type)

    if mesh is None and discovery is None:
        return None

    from niuu.mesh import resolve_peer_id  # noqa: PLC0415
    from niuu.mesh.participant import MeshParticipant  # noqa: PLC0415

    return MeshParticipant(
        mesh=mesh,
        discovery=discovery,
        peer_id=resolve_peer_id(settings.mesh.own_peer_id),
    )
