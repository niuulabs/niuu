"""Focused runtime implementation extracted from :mod:`ravn.cli.commands`."""

from __future__ import annotations

# Dependencies are supplied by the CLI compatibility facade immediately before
# invocation so established command patch points remain effective.
# ruff: noqa: F821


def _build_environment_signal_runtime(
    settings: Settings,
    *,
    drive_loop: Any | None = None,
    persona_config: Any | None = None,
    publisher: Any | None = None,
    mimir: Any | None = None,
    resident_learning_runtime: Any | None = None,
    resident_wakefulness: Any | None = None,
    resident_inbox: Any | None = None,
    owns_publisher: bool = True,
) -> Any | None:
    """Build the resident Environment signal runtime when sources are configured."""
    enabled_sources = [source for source in settings.environment.signal_sources if source.enabled]
    if not enabled_sources:
        return None
    from ravn.environment_signal_runtime import EnvironmentSignalRuntime  # noqa: PLC0415

    if publisher is None:
        publisher = _build_environment_signal_publisher(settings)
    if publisher is None:
        return None

    try:
        output_mode = OutputMode(settings.initiative.default_output_mode)
    except ValueError:
        logger.warning(
            "environment_signals: invalid initiative.default_output_mode=%r; using ambient",
            settings.initiative.default_output_mode,
        )
        output_mode = OutputMode.AMBIENT

    persona = None
    if persona_config is not None:
        persona = getattr(persona_config, "name", None)
    if not persona:
        persona = settings.initiative.default_persona or None

    resident_signal_processor = None
    if resident_inbox is None:
        resident_inbox = _build_resident_inbox(
            settings,
            workspace=_resolve_workspace(settings),
            mimir=mimir,
        )
    resident_signal_recorder = (
        resident_inbox if settings.resident_inbox.environment_signals_enabled else None
    )

    async def _process_resident_signal(event: Any) -> Any:
        result: dict[str, Any] = {}
        if resident_signal_recorder is not None:
            ref = await resident_signal_recorder.write_event(event)
            if resident_wakefulness is not None:
                resident_wakefulness.notify_activity()
            result.update(
                {
                    "residentAutonomySignalPersisted": True,
                    "residentAutonomySignalRef": ref,
                }
            )
        if resident_learning_runtime is not None:
            if resident_wakefulness is not None and resident_signal_recorder is None:
                resident_wakefulness.notify_activity()
            learned = await resident_learning_runtime.process_signal(event)
            if isinstance(learned, dict):
                result.update(learned)
            elif learned is not None:
                result["residentLearningResult"] = learned
        return result or None

    if resident_learning_runtime is not None:
        resident_signal_processor = _process_resident_signal
    elif resident_signal_recorder is not None:
        resident_signal_processor = _process_resident_signal

    return EnvironmentSignalRuntime(
        settings=settings,
        publisher=publisher,
        enqueue=drive_loop.enqueue if drive_loop is not None else None,
        resident_signal_processor=resident_signal_processor,
        persona=persona,
        output_mode=output_mode,
        owns_publisher=owns_publisher,
        durable_home_enabled=resident_signal_recorder is not None,
    )


def _build_resident_inbox(
    settings: Settings,
    *,
    workspace: Path,
    mimir: Any | None,
) -> Any | None:
    """Build the configured durable inbox shared by intake and home turns."""
    if not settings.resident_inbox.enabled:
        return None

    import inspect  # noqa: PLC0415

    cfg = settings.resident_inbox
    cls = _import_class(cfg.adapter)
    kwargs = _inject_secrets(dict(cfg.kwargs), cfg.secret_kwargs_env)
    params = inspect.signature(cls.__init__).parameters
    if "root" in params and "root" not in kwargs:
        kwargs["root"] = _resident_ravn_state_dir(workspace, settings) / "resident-inbox"
    if "mimir" in params and "mimir" not in kwargs:
        if mimir is None:
            raise RuntimeError(
                f"resident inbox adapter {cfg.adapter} requires Mimir, but Mimir is disabled"
            )
        kwargs["mimir"] = mimir
    if "retention_max_pages" in params:
        kwargs.setdefault("retention_max_pages", cfg.signal_retention_max_pages)
    if "retention_max_age_days" in params:
        kwargs.setdefault("retention_max_age_days", cfg.signal_retention_max_age_days)
    if "retention_sweep_interval_seconds" in params:
        kwargs.setdefault(
            "retention_sweep_interval_seconds",
            cfg.signal_retention_sweep_interval_seconds,
        )
    for name, value in (
        ("max_distinct_values", cfg.signal_max_distinct_values),
        ("novelty_min_observations", cfg.signal_novelty_min_observations),
        ("max_invalid_attempts", cfg.signal_max_invalid_attempts),
        ("pending_slot_warn_threshold", cfg.signal_pending_slot_warn_threshold),
    ):
        if name in params:
            kwargs.setdefault(name, value)
    return cls(**kwargs)


async def _build_resident_state(
    settings: Settings,
    *,
    workspace: Path,
    mimir: Any | None,
) -> Any:
    """Select the configured preferred/fallback ResidentStatePort adapters."""
    import inspect  # noqa: PLC0415

    from ravn.adapters.resident_state import select_resident_state  # noqa: PLC0415

    cfg = settings.resident_state
    state_root = _resident_ravn_state_dir(workspace, settings) / "resident-state"

    def _candidate(
        adapter_path: str,
        kwargs: dict[str, Any],
        secret_kwargs_env: dict[str, str],
    ) -> Any | None:
        try:
            cls = _import_class(adapter_path)
            resolved = _inject_secrets(dict(kwargs), secret_kwargs_env)
            params = inspect.signature(cls.__init__).parameters
            if "root" in params and "root" not in resolved:
                resolved["root"] = state_root
            if "mimir" in params and "mimir" not in resolved:
                if mimir is None:
                    raise RuntimeError("adapter requires Mimir but no Mimir backend is configured")
                resolved["mimir"] = mimir
            return cls(**resolved)
        except Exception as exc:
            logger.warning("resident state adapter %s unavailable: %s", adapter_path, exc)
            return None

    candidates = [
        candidate
        for candidate in (
            _candidate(cfg.adapter, cfg.kwargs, cfg.secret_kwargs_env),
            _candidate(
                cfg.fallback_adapter,
                cfg.fallback_kwargs,
                cfg.fallback_secret_kwargs_env,
            ),
        )
        if candidate is not None
    ]
    if not candidates:
        raise RuntimeError("no resident-state adapters could be constructed")
    return await select_resident_state(*candidates)


def _build_resident_runtime(
    settings: Settings,
    *,
    state: Any,
    inbox: Any | None,
) -> Any:
    from ravn.resident_runtime import ResidentRuntime  # noqa: PLC0415

    cfg = settings.resident_state
    return ResidentRuntime(
        state=state,
        inbox=inbox,
        resident_id=(
            settings.mesh.own_peer_id
            or settings.environment.resident_name
            or settings.initiative.default_persona
            or "resident"
        ),
        resident_personality=settings.environment.resident_personality,
        charter=settings.environment.charter,
        max_turns=cfg.continuation_max_turns,
        max_tokens=cfg.continuation_max_tokens,
        context_max_chars=cfg.continuation_context_max_chars,
        tool_result_max_chars=cfg.continuation_tool_result_max_chars,
        scheduled_wake_default_seconds=cfg.scheduled_wake_default_seconds,
        stewardship_interval_seconds=cfg.stewardship_interval_seconds,
        directed_messages_enabled=settings.resident_inbox.directed_messages_enabled,
        environment_id=settings.environment.id,
    )


def _build_resident_learning_runtime(
    settings: Settings,
    *,
    publisher: Any | None,
    workspace: Path,
) -> Any | None:
    """Build the resident learning subscriber/installer for environment Valkyries."""
    if not settings.environment.flocks:
        return None
    if publisher is None or not hasattr(publisher, "subscribe"):
        logger.warning(
            "resident_learning: shared mesh transport is unavailable; "
            "learning adoption will not run"
        )
        return None
    if settings.skill.backend == "sqlite":
        logger.warning("resident_learning: sqlite skill backend is forbidden for Valkyries")
        return None

    from ravn.adapters.skill.file_registry import FileSkillRegistry  # noqa: PLC0415
    from ravn.skills.management import SkillManagementRegistry  # noqa: PLC0415
    from ravn.valkyrie_evolution import (  # noqa: PLC0415
        ResidentLearningIdentity,
        ResidentLearningRuntime,
    )
    from ravn.valkyrie_evolution.learned_tools import (  # noqa: PLC0415
        learned_tool_runner_for_backend,
        learned_tool_venvs_dir,
    )

    resident_id = settings.mesh.own_peer_id or f"valkyrie:{settings.environment.id}"
    local_ravn_dir = _resident_ravn_state_dir(workspace, settings)
    resident_skills_dir = local_ravn_dir / "skills"
    # The resident's own write dir must be searchable, or skills installed by
    # the learning loop are invisible to capability lookup when RAVN_STATE_DIR
    # diverges from the workspace defaults.
    skill_dirs = [
        str(resident_skills_dir),
        *settings.skill.skill_dirs,
        str(workspace / ".ravn" / "skills"),
        str(Path.home() / ".ravn" / "skills"),
    ]
    skill_port = FileSkillRegistry(
        skill_dirs=list(dict.fromkeys(skill_dirs)),
        write_dir=resident_skills_dir,
        include_builtin=settings.skill.include_builtin,
        cwd=workspace,
    )
    skills = SkillManagementRegistry(
        skill_port,
        metadata_path=local_ravn_dir / "skill_management.json",
    )
    identity = ResidentLearningIdentity(
        environment_id=settings.environment.id,
        environment_type=settings.environment.type,
        valkyrie_id=resident_id,
        domain=settings.environment.type,
        flock_ids=list(settings.environment.flocks),
        autonomy_mode=settings.resident_evolution.autonomy_mode,
    )
    reviewer = _build_evolution_adapter(
        settings,
        adapter_path=settings.resident_evolution.reviewer_adapter,
        kwargs=settings.resident_evolution.reviewer_kwargs,
    )
    from ravn.adapters.reflection.flock_learning import FlockLearningStore  # noqa: PLC0415
    from ravn.odin.review import JsonReviewStore, ReviewRequester  # noqa: PLC0415

    review_requester = ReviewRequester(
        publisher=publisher,
        store=JsonReviewStore(local_ravn_dir / "review_outbox.json"),
        source=resident_id,
    )
    return ResidentLearningRuntime(
        identity=identity,
        skills=skills,
        publisher=publisher,
        subscriber=publisher,
        source=resident_id,
        reviewer=reviewer,
        tools_dir=local_ravn_dir / "tools",
        tool_timeout_seconds=settings.resident_evolution.tool_timeout_seconds,
        rollback_consecutive_failures=settings.resident_evolution.rollback_consecutive_failures,
        feedback_confidence_bump=settings.resident_evolution.feedback_confidence_bump,
        skill_inventory_interval_seconds=(
            settings.resident_evolution.skill_inventory_interval_seconds
        ),
        learning_store=FlockLearningStore(local_ravn_dir / "flock_learning.json"),
        review_requester=review_requester,
        learned_tool_runner=learned_tool_runner_for_backend(
            settings.resident_evolution.learned_tool_execution_backend,
            workspace_root=workspace,
            venvs_dir=learned_tool_venvs_dir(local_ravn_dir),
            backend_kwargs=(
                settings.resident_evolution.learned_tool_k8s.model_dump()
                if settings.resident_evolution.learned_tool_execution_backend == "k8s_job"
                else None
            ),
        ),
    )


def _build_realm_capability_sync(
    settings: Settings,
    *,
    subscriber: Any | None,
) -> Any | None:
    """Build the realm capability-ledger writer for resident evolution events.

    Returns None (zero behavior change) when no realm_slug is configured or a
    realm client cannot be built; the realm ledger is advisory bookkeeping, so
    an unusable realm config degrades with a WARNING instead of failing the
    daemon.
    """
    cfg = settings.resident_evolution
    if not cfg.realm_slug:
        return None
    if subscriber is None or not hasattr(subscriber, "subscribe"):
        logger.warning(
            "realm capability sync: realm_slug %r is set but no bus subscriber "
            "is available; the capability ledger will not be updated",
            cfg.realm_slug,
        )
        return None
    client = _realm_client_for(settings)
    if client is None:
        return None

    from ravn.adapters.realm import RealmCapabilitySync  # noqa: PLC0415

    logger.info("realm capability sync: active for realm %s", cfg.realm_slug)
    return RealmCapabilitySync(
        client=client,
        realm_slug=cfg.realm_slug,
        subscriber=subscriber,
    )


def _build_resident_wakefulness(
    settings: Settings,
    *,
    resident_learning_runtime: Any | None,
    publisher: Any | None,
    memory: Any | None = None,
) -> Any | None:
    """Build the wakefulness state machine for a resident daemon."""
    if not settings.resident_wakefulness.enabled:
        return None
    if resident_learning_runtime is None or publisher is None:
        logger.warning(
            "wakefulness: enabled but resident learning runtime or mesh "
            "publisher is unavailable; state machine will not run"
        )
        return None

    from ravn.valkyrie_evolution.wakefulness import ResidentWakefulness  # noqa: PLC0415

    cfg = settings.resident_wakefulness
    return ResidentWakefulness(
        identity=resident_learning_runtime.identity,
        skills=resident_learning_runtime.skills,
        publisher=publisher,
        resident_learning=resident_learning_runtime,
        memory=memory,
        review_requester=resident_learning_runtime.review_requester,
        tick_interval_seconds=cfg.tick_interval_seconds,
        wakeful_window_seconds=cfg.wakeful_window_seconds,
        dream_interval_seconds=cfg.dream_interval_seconds,
        dream_min_idle_seconds=cfg.dream_min_idle_seconds,
        stale_skill_age_seconds=cfg.stale_skill_age_seconds,
        promote_min_successes=cfg.promote_min_successes,
    )


def _build_odin_court(
    settings: Settings,
    *,
    publisher: Any | None,
    memory: Any | None,
    review_requester: Any | None = None,
) -> Any | None:
    """Build the ODIN court resolver for an active resident environment."""
    if not settings.odin_court.enabled:
        return None
    if publisher is None or not hasattr(publisher, "subscribe"):
        return None
    if not settings.environment.flocks and not settings.environment.signal_sources:
        return None

    from ravn.odin import OdinCourt  # noqa: PLC0415
    from ravn.odin.audit import EpisodicCourtAuditSink  # noqa: PLC0415

    audit_sink = EpisodicCourtAuditSink(memory) if memory is not None else None
    return OdinCourt(
        publisher=publisher,
        subscriber=publisher,
        audit_sink=audit_sink,
        review_requester=review_requester,
        court_id=f"odin-court:{settings.environment.id}",
        quorum_size=settings.odin_court.quorum_size,
        timeout_s=settings.odin_court.timeout_seconds,
    )


async def _run_odin_court_sweep(court: Any, interval_seconds: float) -> None:
    """Periodically resolve court cases that aged past their timeout."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await court.sweep_expired()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("odin court: sweep failed")


def _build_feedback_recorder(
    settings: Settings,
    *,
    publisher: Any | None,
    memory: Any | None,
) -> Any | None:
    """Build the feedback-to-episodic-memory recorder for resident daemons."""
    if publisher is None or not hasattr(publisher, "subscribe") or memory is None:
        return None
    if not settings.environment.flocks and not settings.environment.signal_sources:
        return None

    from ravn.feedback import EnvironmentFeedbackRecorder  # noqa: PLC0415

    return EnvironmentFeedbackRecorder(
        subscriber=publisher,
        memory=memory,
        publisher=publisher,
    )


def _build_evolution_adapter(
    settings: Settings,
    *,
    adapter_path: str,
    kwargs: dict[str, Any],
) -> Any:
    """Instantiate an evolution review adapter from config.

    Plain kwargs come straight from YAML.  Adapters whose constructor declares
    an ``llm`` parameter receive the configured LLM adapter — composition-root
    injection, not a YAML value.
    """
    import inspect  # noqa: PLC0415

    cls = _import_class(adapter_path)
    resolved = dict(kwargs)
    parameters = inspect.signature(cls.__init__).parameters
    if "llm" in parameters and "llm" not in resolved:
        resolved["llm"] = _build_llm(settings)
    return cls(**resolved)


def _build_environment_signal_publisher(settings: Settings) -> Any | None:
    """Build the shared publisher used by resident Valkyrie signal and task telemetry."""
    enabled_sources = [source for source in settings.environment.signal_sources if source.enabled]
    if not enabled_sources and not settings.environment.flocks:
        return None
    if not settings.mesh.enabled:
        logger.warning("environment_signals: mesh is disabled; signal sources will not start")
        return None

    from niuu.mesh.transport_builder import build_transport  # noqa: PLC0415

    if settings.mesh.adapters:
        entry = settings.mesh.adapters[0]
        adapter = entry.get("transport", settings.mesh.adapter or "nng")
    else:
        adapter = settings.mesh.adapter or "nng"

    kwargs = _resolve_transport_kwargs(settings, adapter)
    if adapter in ("sleipnir", "rabbitmq") and not kwargs:
        logger.warning("environment_signals: %s transport unavailable", adapter)
        return None

    publisher = build_transport(adapter, **kwargs)
    if publisher is None:
        logger.warning("environment_signals: failed to build %s transport", adapter)
        return None
    if settings.observability.enabled:
        from ravn.adapters.observability import ObservedSleipnirBus  # noqa: PLC0415

        return ObservedSleipnirBus(publisher)
    return publisher


def _wire_triggers(drive_loop: Any, initiative: InitiativeConfig) -> list[Any]:
    """Instantiate trigger adapters from config and register them on drive_loop.

    Loads each entry in ``initiative.trigger_adapters`` via its fully-qualified
    class path (any :class:`~ravn.ports.trigger.TriggerPort` subclass).

    Returns an empty list — cron jobs come exclusively from ``_wire_cron`` now.
    """
    for ta in initiative.trigger_adapters:
        try:
            cls = _import_class(ta.adapter)
            kwargs = _inject_secrets(dict(ta.kwargs), ta.secret_kwargs_env)
            trigger = cls(**kwargs)
            drive_loop.register_trigger(trigger)
            logger.info(
                "trigger adapter registered: %s (name=%r)",
                ta.adapter,
                getattr(trigger, "name", "?"),
            )
        except Exception as exc:
            logger.error("Failed to wire trigger adapter %r: %s", ta.adapter, exc)

    return []
