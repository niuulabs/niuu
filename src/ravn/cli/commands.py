"""Ravn CLI entry point."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import signal
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from ravn.cli.mcp_runtime import (  # noqa: F401
    _mimir_ingest_event_fields_from_mcp_result,
    _mimir_mount_name_from_mcp_server_name,
    _mimir_write_event_fields_from_mcp_result,
    _shutdown_mcp,
    _start_mcp,
    _start_mcp_shared,
)
from ravn.config import ProjectConfig, Settings
from ravn.domain.checkpoint import InterruptReason
from ravn.domain.models import (
    AgentTask,
    Message,
    OutputMode,
    Session,
    TodoItem,
    TodoStatus,
    TokenUsage,
)
from ravn.domain.profile import RavnProfile
from ravn.ports.checkpoint import CheckpointPort
from ravn.ports.executor import ExecutionAgentPort
from ravn.workflow_runtime import (  # noqa: F401
    _dedupe_preserve_order,
    _join_mode_to_fan_in,
    _split_workflow_edge_label,
    _stage_personas,
    _workflow_allowed_outcome_topics,
    _workflow_allowed_task_targets,
    _workflow_event_matches_filters,
    _workflow_graph,
    _workflow_runtime_for_persona,
    _workflow_stage_context,
)

logger = logging.getLogger(__name__)
# Extracted runtime wrappers resolve these legacy module globals at call time.
_RUNTIME_MODEL_EXPORTS = (AgentTask, OutputMode)

app = typer.Typer(
    name="ravn",
    help="Ravn — conversational AI agent with tool calling.",
    add_completion=False,
)

approvals_app = typer.Typer(
    name="approvals",
    help="Manage per-project command approval patterns.",
    add_completion=False,
)
app.add_typer(approvals_app, name="approvals")

from ravn.cli.runtime_config import _configure_logging, _log_effective_config  # noqa: E402
from ravn.cli.warden_commands import (  # noqa: E402, F401
    _parse_deployment_kwargs,
    warden_app,
)

app.add_typer(warden_app, name="warden")

from ravn.cli.flock import flock_app  # noqa: E402 — must be after app is defined

app.add_typer(flock_app, name="flock")

from ravn.cli.registries import personas_app, profiles_app  # noqa: E402

app.add_typer(personas_app, name="personas")
app.add_typer(profiles_app, name="profiles")

from ravn.cli.room import room_app  # noqa: E402

app.add_typer(room_app, name="room")


@app.command("join")
def join(
    persona: str = typer.Option(
        "", "--persona", "-p", help="Persona name, or a path to a persona YAML file."
    ),
    room: str = typer.Option("", "--room", "-r", envvar="RAVN_ROOM", help="Room to join."),
    as_handle: str = typer.Option(
        "", "--as", help="Member handle in the room. Defaults to the persona name."
    ),
    profile: str = typer.Option("", "--profile", help="Profile name or path (deployment wiring)."),
    base_config: str = typer.Option(
        "", "--base-config", help="Existing ravn.yaml to layer the membership config over."
    ),
    rooms_dir: str = typer.Option("", "--rooms-dir", help="Override the rooms state directory."),
    here: bool = typer.Option(
        False, "--here", help="Run the member in this terminal instead of detaching."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Replace an existing member with the same handle."
    ),
    autonomous: bool = typer.Option(
        False,
        "--autonomous",
        help="Also enable the self-driving triggers (cron, staleness, wakefulness).",
    ),
) -> None:
    """Put a persona-typed Ravn into a room as an addressable member.

    Persona picks who the member is; profile picks how it is deployed.  The
    command waits until the member actually registers with the room, so a
    reported join is a join.

    \b
    Examples:
      ravn join --persona reviewer
      ravn join --persona reviewer --room desk --as second-opinion
      ravn join --persona ./contrib/red-team.yaml --room desk
      ravn join --persona coder --room desk --here
    """
    from ravn.cli.room import join_room

    join_room(persona, room, as_handle, profile, base_config, rooms_dir, here, force, autonomous)


@app.command("leave")
def leave(
    as_handle: str = typer.Option(..., "--as", help="Member handle to remove from the room."),
    room: str = typer.Option("", "--room", "-r", envvar="RAVN_ROOM", help="Room to leave."),
    rooms_dir: str = typer.Option("", "--rooms-dir", help="Override the rooms state directory."),
) -> None:
    """Stop a member's daemon and remove it from the room.

    \b
    Examples:
      ravn leave --as reviewer
      ravn leave --as reviewer --room desk
    """
    from ravn.cli.room import leave_room

    leave_room(as_handle, room, rooms_dir)


def approvals_main() -> None:
    approvals_app()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


# Compatibility exports remain late because the Typer apps must exist first.
# isort: off
from ravn.cli.runtime_builders import (  # noqa: E402, F401
    _REALM_CLIENT_CACHE,
    _RealmBuildConfig,
    _attach_agent_build_tool,
    _build_executor,
    _build_llm,
    _build_memory,
    _build_mimir,
    _build_mimir_auth,
    _build_permission,
    _build_tool_build_backend,
    _constructor_accepts_kwarg,
    _get_tool_group,
    _import_class,
    _inject_secrets,
    _mimir_workload_platform_defaults,
    _realm_client_for,
    _resident_ravn_state_dir,
    _resolve_mimir_auth_token,
    _resolve_realm_build_config,
    _resolve_workspace,
    _run_coro_blocking,
    _runtime_cli_transport_kwargs,
    _transport_mcp_servers,
    _uses_cli_transport_executor,
    _uses_cli_transport_runtime,
)
from ravn.cli.tool_builders import (  # noqa: E402, F401
    _MIMIR_TOOL_NAMES,
    _apply_trust_filter,
    _build_compressor,
    _build_hooks,
    _build_prompt_builder,
    _build_tools,
    _build_workflow_capability_sources,
    _expand_allowed_tools,
    _filter_tools,
    _groups_for_persona,
    _in_groups,
    _load_resident_learned_tools,
)
# isort: on


# Agent/session composition remains here because its symbols are public CLI patch points.


async def _cli_user_input(question: str) -> str:
    """Prompt the user for input during ask_user tool calls."""
    return input(f"\n[Ravn asks] {question}\nYou: ").strip()


# ---------------------------------------------------------------------------
# Persona resolution
# ---------------------------------------------------------------------------


def _looks_like_path(name: str) -> bool:
    """Return True when *name* addresses a file rather than a registry name.

    Registry names are bare slugs (``reviewer``), so anything carrying a path
    separator or a YAML suffix is an explicit file reference.
    """
    return os.sep in name or name.endswith((".yaml", ".yml"))


def _resolve_persona(
    persona_name: str,
    project_config: ProjectConfig | None,
    settings: Settings | None = None,
    cwd: Path | None = None,
    warn: bool = True,
) -> Any:
    """Load and merge a persona with optional ProjectConfig overrides.

    *persona_name* is either a registry name resolved through the configured
    ``persona_source`` adapter, or a path to a persona YAML file.  Returns
    ``None`` when the name is empty or unresolvable — callers that act on an
    explicit operator flag should use :func:`_require_persona` instead.

    *warn* suppresses the fallback warning for callers that report the failure
    themselves, so an operator never sees both a warning and an error.
    """
    from niuu.utils import import_class, resolve_secret_kwargs  # noqa: PLC0415
    from ravn.adapters.personas.loader import FilesystemPersonaAdapter  # noqa: PLC0415

    if settings is not None:
        cfg = settings.persona_source
        cls = import_class(cfg.adapter)
        kwargs = resolve_secret_kwargs(cfg.kwargs, cfg.secret_kwargs_env)
        loader = cls(**kwargs)
    else:
        loader = FilesystemPersonaAdapter(cwd=cwd)

    name = persona_name.strip() or (
        project_config.persona.strip() if project_config is not None else ""
    )
    if not name:
        return None

    if _looks_like_path(name):
        # A path is inherently a filesystem reference, so it bypasses the
        # configured registry adapter (which may not be filesystem-backed).
        persona = FilesystemPersonaAdapter(cwd=cwd).load_path(Path(name).expanduser())
    else:
        persona = loader.load(name)
    if persona is None:
        if warn:
            typer.echo(f"Warning: persona '{name}' not found — using defaults.", err=True)
        return None

    # Apply per-sidecar overrides injected by Volundr at flock dispatch time.
    # These live in settings.persona_overrides and are only present when the
    # sidecar YAML was generated with per-persona system_prompt_extra /
    # iteration_budget overrides (NIU-638).
    if settings is not None:
        from ravn.adapters.personas.overrides import apply_config_overrides  # noqa: PLC0415

        overrides = settings.persona_overrides.model_dump(exclude_defaults=True)
        if overrides:
            persona = apply_config_overrides(persona, overrides)

    if project_config is not None:
        # merge() is a pure data transform on PersonaConfig + ProjectConfig,
        # not adapter-specific — safe to call on the concrete class directly.
        persona = FilesystemPersonaAdapter.merge(persona, project_config)

    return persona


def _require_persona(
    persona_name: str,
    project_config: ProjectConfig | None,
    settings: Settings | None = None,
    cwd: Path | None = None,
) -> Any:
    """Resolve a persona, exiting when an explicitly named one does not resolve.

    An operator who passes ``--persona`` has stated which role they want;
    silently continuing with defaults would run a different agent than asked
    for.  A persona inherited from ProjectConfig stays a soft fallback, and an
    empty name still returns ``None``.
    """
    name = persona_name.strip()
    persona = _resolve_persona(
        persona_name, project_config, settings=settings, cwd=cwd, warn=not name
    )
    if persona is not None or not name:
        return persona

    typer.echo(f"Error: persona '{name}' not found.", err=True)
    if not _looks_like_path(name):
        typer.echo("Run 'ravn personas list' to see available personas.", err=True)
    raise typer.Exit(2)


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


def _resolve_profile(profile_name: str, warn: bool = True) -> RavnProfile | None:
    """Load a RavnProfile by name or path.

    Names resolve from ``~/.ravn/profiles/`` then the built-in set; anything
    path-shaped is read directly.  Returns ``None`` when *profile_name* is
    empty or cannot be resolved, so callers can proceed using Settings
    defaults.  *warn* suppresses the fallback warning for callers that report
    the failure themselves.
    """
    from ravn.adapters.profiles.loader import ProfileLoader

    name = profile_name.strip()
    if not name:
        return None

    loader = ProfileLoader()
    if _looks_like_path(name):
        profile = loader.load_from_file(Path(name).expanduser())
    else:
        profile = loader.load(name)
    if profile is None:
        if warn:
            typer.echo(f"Warning: profile '{name}' not found — using defaults.", err=True)
        return None

    return profile


def _require_profile(profile_name: str) -> RavnProfile | None:
    """Resolve a profile, exiting when an explicitly named one does not resolve.

    Mirrors :func:`_require_persona`: an explicit ``--profile`` that cannot be
    resolved is an error, not a silent fallback to defaults.
    """
    name = profile_name.strip()
    profile = _resolve_profile(profile_name, warn=False)
    if profile is not None or not name:
        return profile

    typer.echo(f"Error: profile '{name}' not found.", err=True)
    if not _looks_like_path(name):
        typer.echo("Run 'ravn profiles list' to see available profiles.", err=True)
    raise typer.Exit(2)


def _apply_profile(
    profile: RavnProfile,
    settings: Settings,
    *,
    persona_config: Any | None,
) -> tuple[str, int, int]:
    """Apply profile overrides and return (system_prompt, max_iterations, max_tokens).

    The profile's ``system_prompt_extra`` is appended after the persona template
    (or the settings default prompt) when non-empty.  The profile also enables
    checkpoint and filters MCP servers in-place on *settings*.

    Returns the resolved (system_prompt, max_iterations, max_tokens) triple so
    callers don't need to re-derive persona overrides separately.
    """
    system_prompt = settings.agent.system_prompt
    max_iterations = settings.agent.max_iterations
    max_tokens = settings.effective_max_tokens()

    if persona_config is not None:
        if persona_config.system_prompt_template:
            system_prompt = persona_config.system_prompt_template
        if persona_config.iteration_budget is not None:
            max_iterations = persona_config.iteration_budget
        if persona_config.llm.max_tokens:
            max_tokens = persona_config.llm.max_tokens

    if profile.system_prompt_extra:
        system_prompt = f"{system_prompt}\n\n{profile.system_prompt_extra}"

    if profile.checkpoint_enabled:
        settings.checkpoint.enabled = True

    if profile.mcp_servers:
        settings.mcp_servers = [s for s in settings.mcp_servers if s.name in profile.mcp_servers]

    return system_prompt, max_iterations, max_tokens


def _build_iteration_budget(settings: Settings, max_iterations: int) -> Any | None:
    """Build the cross-turn budget, or disable it for unbounded agents."""
    if max_iterations <= 0 or settings.iteration_budget.total <= 0:
        return None
    from ravn.budget import IterationBudget

    return IterationBudget(
        total=settings.iteration_budget.total,
        near_limit_threshold=settings.iteration_budget.near_limit_threshold,
    )


def _resolve_persona_model(settings: Settings, persona_config: Any | None) -> str:
    """Resolve the model for a persona-backed agent."""
    if persona_config is not None and getattr(persona_config.llm, "primary_alias", ""):
        return str(persona_config.llm.primary_alias)
    return settings.effective_model()


# ---------------------------------------------------------------------------
# Builder: Checkpoint
# ---------------------------------------------------------------------------


def _build_checkpoint(settings: Settings) -> CheckpointPort:
    """Build the checkpoint adapter from config.

    Backend selection:
    * ``checkpoint.backend = 'postgres'`` (or memory backend = postgres with DSN):
      PostgresCheckpointAdapter.
    * Otherwise: DiskCheckpointAdapter (Pi / local mode).
    """
    from ravn.adapters.checkpoint.disk import DiskCheckpointAdapter

    cp = settings.checkpoint
    max_snap = cp.max_checkpoints_per_task

    # Prefer explicit checkpoint.backend setting, fall back to memory.backend heuristic.
    use_postgres = cp.backend == "postgres"
    if not use_postgres and settings.memory.backend == "postgres":
        use_postgres = True

    if use_postgres:
        dsn = os.environ.get(settings.memory.dsn_env, "") if settings.memory.dsn_env else ""
        dsn = dsn or settings.memory.dsn
        if dsn:
            from ravn.adapters.checkpoint.postgres import PostgresCheckpointAdapter

            return PostgresCheckpointAdapter(dsn=dsn, max_snapshots_per_task=max_snap)

    return DiskCheckpointAdapter(checkpoint_dir=cp.dir, max_snapshots_per_task=max_snap)


# ---------------------------------------------------------------------------
# Agent assembly
# ---------------------------------------------------------------------------


def _build_agent(
    settings: Settings,
    *,
    no_tools: bool = False,
    persona_config: Any | None = None,
    profile: RavnProfile | None = None,
    session: Session | None = None,
    task_id: str | None = None,
    sleipnir_publisher: object | None = None,
) -> tuple[ExecutionAgentPort, Any]:
    from ravn.adapters.channels.composite import CompositeChannel
    from ravn.adapters.cli_channel import CliChannel
    from ravn.ports.channel import ChannelPort

    # Apply profile overrides to settings and derive resolved prompts/limits.
    if profile is not None:
        system_prompt, max_iterations, max_tokens = _apply_profile(
            profile, settings, persona_config=persona_config
        )
    else:
        system_prompt = settings.agent.system_prompt
        max_iterations = settings.agent.max_iterations
        max_tokens = settings.effective_max_tokens()
        if persona_config is not None:
            if persona_config.system_prompt_template:
                system_prompt = persona_config.system_prompt_template
            if persona_config.iteration_budget is not None:
                max_iterations = persona_config.iteration_budget
            if persona_config.llm.max_tokens:
                max_tokens = persona_config.llm.max_tokens
    resolved_model = _resolve_persona_model(settings, persona_config)

    workspace = _resolve_workspace(settings)
    permission_mode = settings.permission.mode
    if persona_config is not None and persona_config.permission_mode:
        permission_mode = persona_config.permission_mode
    cli_transport_executor = _uses_cli_transport_executor(persona_config)
    llm = None if cli_transport_executor else _build_llm(settings)
    session = session or Session()
    base_channel: CliChannel = CliChannel()
    channel: ChannelPort = base_channel
    if settings.sleipnir.enabled:
        from ravn.adapters.channels.sleipnir import SleipnirChannel

        sleipnir_ch = SleipnirChannel(
            settings.sleipnir,
            session_id=str(session.id),
            task_id=None,
        )
        channel = CompositeChannel([base_channel, sleipnir_ch])
    permission = _build_permission(
        settings,
        workspace,
        no_tools=no_tools,
        persona_config=persona_config,
    )
    memory = _build_memory(settings, llm=llm)
    mimir = _build_mimir(settings)
    iteration_budget = _build_iteration_budget(settings, max_iterations)
    tools = _build_tools(
        settings,
        workspace,
        session,
        llm,
        memory,
        iteration_budget,
        mimir,
        no_tools=no_tools,
        persona_config=persona_config,
        permission=permission,
    )
    compressor = None if cli_transport_executor else _build_compressor(settings, llm)
    prompt_builder = _build_prompt_builder(settings)
    pre_hooks, post_hooks = _build_hooks(settings)
    checkpoint_port = _build_checkpoint(settings)

    extended_thinking = (
        settings.llm.extended_thinking
        if settings.llm.extended_thinking.enabled and not cli_transport_executor
        else None
    )

    cp_cfg = settings.checkpoint
    executor = _build_executor(persona_config)
    agent = executor.build(
        llm=llm,
        tools=tools,
        channel=channel,
        permission=permission,
        permission_mode=permission_mode,
        system_prompt=system_prompt,
        model=resolved_model,
        max_tokens=max_tokens,
        max_iterations=max_iterations,
        workspace_dir=str(workspace),
        mcp_servers=_transport_mcp_servers(settings),
        session=session,
        pre_tool_hooks=pre_hooks or None,
        post_tool_hooks=post_hooks or None,
        user_input_fn=_cli_user_input,
        memory=memory,
        mimir=mimir,
        episode_summary_max_chars=settings.agent.episode_summary_max_chars,
        episode_task_max_chars=settings.agent.episode_task_max_chars,
        iteration_budget=iteration_budget,
        compressor=compressor,
        prompt_builder=prompt_builder,
        reflection_model=settings.effective_memory_reflection_model(),
        reflection_max_tokens=settings.memory.reflection_max_tokens,
        task_summary_max_chars=settings.memory.task_summary_max_chars,
        input_token_cost_per_million=settings.memory.input_token_cost_per_million,
        output_token_cost_per_million=settings.memory.output_token_cost_per_million,
        extended_thinking=extended_thinking,
        checkpoint_port=checkpoint_port if cp_cfg.enabled else None,
        task_id=task_id,
        checkpoint_every_n_tools=cp_cfg.checkpoint_every_n_tools,
        auto_checkpoint_before_destructive=cp_cfg.auto_before_destructive,
        budget_milestone_fractions=cp_cfg.budget_milestone_fractions,
        sleipnir_publisher=sleipnir_publisher,
        persona=persona_config.name if persona_config else "",
        persona_config=persona_config,
        stop_on_outcome=persona_config.stop_on_outcome if persona_config else False,
        max_prompt_tokens=settings.context_management.max_prompt_tokens,
        max_tool_result_chars=settings.tools.max_result_chars,
    )

    return agent, channel


def _make_slash_ctx(agent: ExecutionAgentPort, settings: Settings) -> Any:
    """Build a SlashCommandContext from the running agent and loaded settings."""
    from ravn.adapters.slash_commands import SlashCommandContext

    return SlashCommandContext(
        session=agent.session,
        tools=agent.tools,
        max_iterations=agent.max_iterations,
        llm_adapter_name=agent.llm_adapter_name,
        permission_mode=settings.permission.mode,
        checkpoint_port=agent.checkpoint_port,
        task_id=agent.task_id,
    )


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command()
def run(
    prompt: str = typer.Argument(default="", help="Initial prompt. If empty, starts REPL."),
    no_tools: bool = typer.Option(False, "--no-tools", help="Disable all tool execution."),
    show_usage: bool = typer.Option(False, "--show-usage", help="Print token usage after turn."),
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    persona: str = typer.Option(
        "", "--persona", "-p", help="Persona name (built-in or from ~/.ravn/personas/)."
    ),
    profile: str = typer.Option(
        "", "--profile", help="Profile name (built-in or from ~/.ravn/profiles/)."
    ),
    resume: str = typer.Option(
        "",
        "--resume",
        "-r",
        help="Resume an interrupted task by its task_id.",
    ),
) -> None:
    """Start a Ravn conversation. Pass a prompt for single-turn, or omit for REPL."""
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)
    project_config = ProjectConfig.discover()
    ravn_profile = _require_profile(profile)
    # --persona overrides the profile's persona reference; if neither is given
    # the persona is taken from the profile (or ProjectConfig as fallback).
    effective_persona = persona or (ravn_profile.persona if ravn_profile else "")
    persona_config = _require_persona(effective_persona, project_config, settings=settings)

    asyncio.run(
        _run_with_signals(
            settings=settings,
            no_tools=no_tools,
            persona_config=persona_config,
            profile=ravn_profile,
            prompt=prompt,
            show_usage=show_usage,
            resume_task_id=resume.strip() or None,
            resume_checkpoint_id=None,
        )
    )


async def _run_with_signals(
    *,
    settings: Settings,
    no_tools: bool,
    persona_config: Any | None,
    profile: RavnProfile | None = None,
    prompt: str,
    show_usage: bool,
    resume_task_id: str | None,
    resume_checkpoint_id: str | None = None,
) -> None:
    """Build the agent, install signal handlers, and run the conversation."""
    # Optionally load a checkpoint for resume.
    restored_session: Session | None = None
    restored_prompt: str = prompt
    if resume_task_id is not None:
        restored_session, restored_prompt = await _load_checkpoint_session(
            settings,
            resume_task_id,
            fallback_prompt=prompt,
            checkpoint_id=resume_checkpoint_id,
        )

    # NIU-598: create in-process bus for post-session reflection (standalone CLI mode).
    in_process_bus: Any | None = None
    if settings.reflection.enabled:
        from sleipnir.adapters.in_process import InProcessBus

        in_process_bus = InProcessBus()

    agent, channel = _build_agent(
        settings,
        no_tools=no_tools,
        persona_config=persona_config,
        profile=profile,
        session=restored_session,
        task_id=resume_task_id,
        sleipnir_publisher=in_process_bus,
    )

    # NIU-598: start post-session reflection service after agent is built.
    reflection_svc: Any | None = None
    if in_process_bus is not None:
        _refl_mimir = _build_mimir(settings)
        if _refl_mimir is not None:
            from ravn.adapters.reflection.post_session import PostSessionReflectionService

            reflection_svc = PostSessionReflectionService(
                subscriber=in_process_bus,
                mimir=_refl_mimir,
                llm=_build_llm(settings),
                config=settings.effective_post_session_reflection_config(),
            )
            await reflection_svc.start()

    # Register signal handlers after agent is built so they can call agent.interrupt().
    def _on_signal(reason: InterruptReason) -> None:
        agent.interrupt(reason)
        typer.echo(
            f"\n[ravn] Interrupt received ({reason}) — finishing current tool call …",
            err=True,
        )

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: _on_signal(InterruptReason.SIGINT))
    loop.add_signal_handler(signal.SIGTERM, lambda: _on_signal(InterruptReason.SIGTERM))

    try:
        await _chat(
            agent,
            channel,
            settings=settings,
            prompt=restored_prompt,
            show_usage=show_usage,
        )
    except KeyboardInterrupt:
        return
    finally:
        # Emit resume hint when an interrupt was received.
        if agent._interrupt_reason is not None:
            typer.echo(
                f"\n[ravn] State saved. Resume with: ravn --resume {agent.task_id}",
                err=True,
            )
        # NIU-598: flush pending events then tear down the reflection service.
        if in_process_bus is not None:
            with suppress(Exception):
                await in_process_bus.flush()
        if reflection_svc is not None:
            await reflection_svc.stop()


async def _load_checkpoint_session(
    settings: Settings,
    task_id: str,
    *,
    fallback_prompt: str,
    checkpoint_id: str | None = None,
) -> tuple[Session | None, str]:
    """Load a checkpoint and reconstruct a Session from it.

    When *checkpoint_id* is provided, loads a named snapshot; otherwise loads
    the crash-recovery checkpoint for *task_id*.

    Returns ``(session, user_input)`` where ``user_input`` is the original
    prompt from the checkpoint (or *fallback_prompt* if no checkpoint exists).
    """
    port = _build_checkpoint(settings)

    if checkpoint_id:
        checkpoint = await port.load_snapshot(checkpoint_id)
        if checkpoint is None:
            typer.echo(f"[ravn] No snapshot found for checkpoint_id={checkpoint_id!r}", err=True)
            return None, fallback_prompt
    else:
        checkpoint = await port.load(task_id)
        if checkpoint is None:
            typer.echo(f"[ravn] No checkpoint found for task_id={task_id!r}", err=True)
            return None, fallback_prompt

    # Reconstruct session from checkpoint messages.
    session = Session()
    for raw_msg in checkpoint.messages:
        session.messages.append(Message(role=raw_msg["role"], content=raw_msg["content"]))

    # Restore todo list.
    for raw_todo in checkpoint.todos:
        status_raw = raw_todo.get("status", "pending")
        try:
            status = TodoStatus(status_raw)
        except ValueError:
            status = TodoStatus.PENDING
        session.upsert_todo(
            TodoItem(
                id=raw_todo["id"],
                content=raw_todo["content"],
                status=status,
                priority=raw_todo.get("priority", 0),
            )
        )

    typer.echo(
        f"[ravn] Resuming task {task_id!r} from checkpoint "
        f"({len(session.messages)} messages, "
        f"{checkpoint.iteration_budget_consumed}/{checkpoint.iteration_budget_total} "
        f"iterations consumed)",
        err=True,
    )

    return session, checkpoint.user_input


async def _chat(
    agent: ExecutionAgentPort,
    channel: Any,
    *,
    settings: Settings,
    prompt: str,
    show_usage: bool,
    interaction_tracker: Any | None = None,
) -> None:
    """Run a single-turn or multi-turn conversation."""
    from ravn.adapters.slash_commands import handle as handle_slash

    mcp_manager = await _start_mcp(settings, agent)
    try:
        if prompt:
            await _run_turn(agent, channel, prompt, show_usage=show_usage, single_turn=True)
            return

        # REPL mode.
        typer.echo("Ravn — type your message or /help for commands. Ctrl+D to exit.\n")
        slash_ctx = _make_slash_ctx(agent, settings)
        while True:
            try:
                user_input = input("You: ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            slash_output = handle_slash(user_input, slash_ctx)
            if slash_output is not None:
                typer.echo(slash_output)
                continue

            if interaction_tracker is not None:
                interaction_tracker.touch()
            await _run_turn(agent, channel, user_input, show_usage=show_usage)
    finally:
        await _shutdown_mcp(mcp_manager)


async def _run_turn(
    agent: ExecutionAgentPort,
    channel: Any,
    user_input: str,
    *,
    show_usage: bool,
    single_turn: bool = False,
) -> None:
    try:
        result = await agent.run_turn(user_input)
        channel.finish()
        if show_usage:
            _print_usage(result.usage)
    except Exception as exc:
        channel.finish()
        typer.echo(f"\n[error] {exc}", err=True)
        if single_turn:
            sys.exit(1)


@app.command("tool-mcp")
def tool_mcp(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    persona: str = typer.Option(
        "", "--persona", "-p", help="Persona whose allowed tools should be exposed."
    ),
    profile: str = typer.Option(
        "", "--profile", help="Profile name (built-in or from ~/.ravn/profiles/)."
    ),
    conversation_id: str = typer.Option("", "--conversation-id", hidden=True),
    task_id: str = typer.Option("", "--task-id", hidden=True),
    traceparent: str = typer.Option("", "--traceparent", hidden=True),
    tracestate: str = typer.Option("", "--tracestate", hidden=True),
) -> None:
    """Serve the active Ravn ToolPort set over MCP stdio."""
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)
    from niuu.observability import configure_observability, shutdown_observability

    configure_observability(
        settings.observability,
        resource_attributes={
            "service.instance.id": settings.mesh.own_peer_id or settings.environment.id,
            "deployment.environment.name": settings.environment.id,
            "ravn.environment.id": settings.environment.id,
            "ravn.environment.type": settings.environment.type,
            "ravn.runtime.component": "tool_mcp",
        },
    )
    try:
        project_config = ProjectConfig.discover()
        ravn_profile = _require_profile(profile)
        effective_persona = persona or (ravn_profile.persona if ravn_profile else "")
        persona_config = _require_persona(effective_persona, project_config, settings=settings)
        tools = _build_tool_mcp_tools(settings, persona_config=persona_config)

        from ravn.adapters.mcp.tool_port_server import ToolPortMcpServer

        server = ToolPortMcpServer(
            tools,
            agent_name=effective_persona or "ravn",
            conversation_id=conversation_id,
            task_id=task_id,
            trace_carrier={
                key: value
                for key, value in (
                    ("traceparent", traceparent),
                    ("tracestate", tracestate),
                )
                if value
            },
        )
        allowed_tools = _expand_allowed_tools(
            set(persona_config.allowed_tools or []) if persona_config is not None else set()
        )
        _attach_agent_build_tool(
            server,
            _resolve_workspace(settings),
            enabled="build_tool" in allowed_tools,
            settings=settings,
        )
        asyncio.run(server.run_stdio())
    finally:
        shutdown_observability()


def _build_tool_mcp_tools(settings: Settings, *, persona_config: Any | None) -> list[Any]:
    workspace = _resolve_workspace(settings)
    session = Session()
    memory = (
        _build_memory(settings) if _tool_mcp_allows(persona_config, _MEMORY_TOOL_NAMES) else None
    )
    mimir = _build_mimir(settings) if _tool_mcp_allows(persona_config, _MIMIR_TOOL_NAMES) else None
    max_iterations = settings.agent.max_iterations
    if persona_config is not None and persona_config.iteration_budget is not None:
        max_iterations = persona_config.iteration_budget
    return _build_tools(
        settings,
        workspace,
        session,
        llm=None,
        memory=memory,
        iteration_budget=_build_iteration_budget(settings, max_iterations),
        mimir=mimir,
        persona_config=persona_config,
        permission=_build_permission(
            settings,
            workspace,
            no_tools=False,
            persona_config=persona_config,
        ),
    )


_MEMORY_TOOL_NAMES = {"ravn_memory_search", "session_search"}


def _tool_mcp_allows(persona_config: Any | None, names: set[str] | list[str]) -> bool:
    if persona_config is None or not getattr(persona_config, "allowed_tools", None):
        return True
    allowed = _expand_allowed_tools(set(persona_config.allowed_tools or []))
    return any(_in_groups(name, allowed) for name in names)


def _print_usage(usage: TokenUsage) -> None:
    parts = [f"in={usage.input_tokens}", f"out={usage.output_tokens}"]
    if usage.cache_read_tokens:
        parts.append(f"cache_read={usage.cache_read_tokens}")
    if usage.cache_write_tokens:
        parts.append(f"cache_write={usage.cache_write_tokens}")
    typer.echo(f"[tokens] {', '.join(parts)}")


# ---------------------------------------------------------------------------
# Approvals CLI
# ---------------------------------------------------------------------------


@approvals_app.command("list")
def approvals_list() -> None:
    """List all stored approval patterns for the current project."""
    from ravn.adapters.memory.approval import ApprovalMemory

    memory = ApprovalMemory()
    entries = memory.list_entries()
    if not entries:
        typer.echo("No approval patterns stored.")
        return
    typer.echo(f"Approval patterns ({len(entries)}):\n")
    for entry in entries:
        auto = entry.auto_approved_count
        typer.echo(f"  {entry.command!r}")
        typer.echo(f"    pattern      : {entry.pattern}")
        typer.echo(f"    approved_at  : {entry.approved_at}")
        typer.echo(f"    auto-approved: {auto} time(s)\n")


@approvals_app.command("revoke")
def approvals_revoke(
    pattern: str = typer.Argument(help="Command text or pattern to revoke."),
) -> None:
    """Revoke an approval pattern so the command will be prompted again."""
    from ravn.adapters.memory.approval import ApprovalMemory

    memory = ApprovalMemory()
    removed = memory.revoke(pattern)
    if removed:
        typer.echo(f"Revoked: {pattern!r}")
    else:
        typer.echo(f"No matching approval found for {pattern!r}", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Resume CLI (NIU-537)
# ---------------------------------------------------------------------------


@app.command()
def resume(
    task_id: str = typer.Argument(help="task_id to resume from its latest checkpoint."),
    checkpoint_id: str = typer.Option(
        "",
        "--checkpoint",
        "-c",
        help="Specific checkpoint_id to restore (defaults to latest crash-recovery checkpoint).",
    ),
    config: str = typer.Option("", "--config", help="Path to ravn config YAML."),
    show_usage: bool = typer.Option(False, "--show-usage", help="Print token usage after turn."),
) -> None:
    """Resume a task from a checkpoint.

    Loads the crash-recovery checkpoint (or a named snapshot when --checkpoint
    is given) and re-enters the REPL at the point the task was interrupted.
    """
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)
    project_config = ProjectConfig.discover()
    persona_config = _resolve_persona("", project_config, settings=settings)

    asyncio.run(
        _run_with_signals(
            settings=settings,
            no_tools=False,
            persona_config=persona_config,
            profile=None,
            prompt="",
            show_usage=show_usage,
            resume_task_id=task_id.strip(),
            resume_checkpoint_id=checkpoint_id.strip() or None,
        )
    )


# ---------------------------------------------------------------------------
# Evolution CLI
# ---------------------------------------------------------------------------


evolve_app = typer.Typer(
    name="evolve",
    help="Self-improvement pattern extraction.",
    add_completion=False,
    invoke_without_command=True,
)
app.add_typer(evolve_app, name="evolve")


@evolve_app.callback(invoke_without_command=True)
def evolve(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
) -> None:
    """Run the self-improvement pattern extraction pass.

    Analyses accumulated task outcomes and episodic memory to surface
    recurring tool sequences (skill suggestions), systematic errors
    (warnings), and effective strategies.  Results are printed as a
    human-readable diff — nothing is modified automatically.
    """
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)

    if not settings.evolution.enabled:
        typer.echo("Evolution is disabled in config (evolution.enabled = false).")
        raise typer.Exit(0)

    asyncio.run(_run_evolve(settings))


async def _run_evolve(settings: Settings) -> None:
    from ravn.context.evolution import (
        PatternExtractor,
        load_state,
        save_state,
        should_run,
    )

    memory = _build_memory(settings)
    if memory is None:
        typer.echo("Memory backend not available — evolution requires memory.", err=True)
        raise typer.Exit(1)

    evo = settings.evolution
    state_path = Path(evo.state_path).expanduser()
    state = load_state(state_path)
    current_count = await memory.count_episodes()

    if not should_run(state, current_count, min_new=evo.min_new_outcomes):
        typer.echo(
            f"Not enough new episodes ({current_count - state.outcome_count_at_last_run} "
            f"since last run, need {evo.min_new_outcomes})."
        )
        return

    typer.echo(
        f"Analysing {current_count} episodes "
        f"({current_count - state.outcome_count_at_last_run} new)..."
    )

    extractor = PatternExtractor(
        memory,
        max_episodes_to_analyze=evo.max_episodes_to_analyze,
        skill_suggestion_min_occurrences=evo.skill_suggestion_min_occurrences,
        error_warning_min_occurrences=evo.error_warning_min_occurrences,
        strategy_min_occurrences=evo.strategy_min_occurrences,
        max_skill_suggestions=evo.max_skill_suggestions,
        max_system_warnings=evo.max_system_warnings,
        max_strategy_injections=evo.max_strategy_injections,
    )
    evolution = await extractor.extract()

    if evolution.is_empty():
        typer.echo("No patterns found.")
    else:
        typer.echo(evolution.as_diff())

    state.outcome_count_at_last_run = current_count
    state.last_run_at = datetime.now(UTC)
    save_state(state_path, state)
    typer.echo("Evolution state saved.")


# ---------------------------------------------------------------------------
# Gateway CLI
# ---------------------------------------------------------------------------


gateway_app = typer.Typer(
    name="gateway",
    help="Start the Ravn Pi-mode gateway (Telegram polling + local HTTP).",
    add_completion=False,
)
app.add_typer(gateway_app, name="gateway")


@gateway_app.command()
def gateway(
    telegram: bool = typer.Option(False, "--telegram", help="Enable Telegram polling channel."),
    http: bool = typer.Option(False, "--http", help="Enable local HTTP channel."),
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    persona: str = typer.Option(
        "", "--persona", "-p", help="Persona name applied to all gateway sessions."
    ),
    profile: str = typer.Option(
        "", "--profile", help="Profile name (built-in or from ~/.ravn/profiles/)."
    ),
) -> None:
    """Start the Ravn gateway (Telegram polling + local HTTP server).

    Channels are enabled via flags or via the ``gateway:`` section of ravn.yaml.
    The gateway runs as asyncio tasks — no separate process required.

    Example config (ravn.yaml)::

        gateway:
          enabled: true
          channels:
            telegram:
              enabled: true
              token_env: TELEGRAM_BOT_TOKEN
              allowed_chat_ids: [123456789]
            http:
              enabled: true
              host: 0.0.0.0
              port: 7477
    """
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)
    project_config = ProjectConfig.discover()
    ravn_profile = _require_profile(profile)
    effective_persona = persona or (ravn_profile.persona if ravn_profile else "")
    persona_config = _require_persona(effective_persona, project_config, settings=settings)

    # CLI flags override config file.
    if telegram:
        settings.gateway.channels.telegram.enabled = True
    if http:
        settings.gateway.channels.http.enabled = True

    if (
        not settings.gateway.channels.telegram.enabled
        and not settings.gateway.channels.http.enabled
    ):
        typer.echo(
            "No channels enabled. Use --telegram, --http, or set gateway.channels in config.",
            err=True,
        )
        raise typer.Exit(1)

    asyncio.run(_run_gateway(settings, persona_config=persona_config, profile=ravn_profile))


def _make_channel_tasks(
    channels_cfg: Any,
    gw: Any,
) -> list[tuple[Any, str]]:
    """Create asyncio tasks for the four extended gateway channel adapters.

    Returns a list of ``(task, name)`` pairs so callers can populate both
    a task list and a display list without duplicating the if-chain.
    """
    from ravn.adapters.channels.gateway_discord import DiscordGateway
    from ravn.adapters.channels.gateway_matrix import MatrixGateway
    from ravn.adapters.channels.gateway_slack import SlackGateway
    from ravn.adapters.channels.gateway_whatsapp import WhatsAppGateway

    pairs: list[tuple[Any, str]] = []
    if channels_cfg.discord.enabled:
        task = asyncio.create_task(DiscordGateway(channels_cfg.discord, gw).run(), name="discord")
        pairs.append((task, "discord"))
    if channels_cfg.slack.enabled:
        task = asyncio.create_task(SlackGateway(channels_cfg.slack, gw).run(), name="slack")
        pairs.append((task, "slack"))
    if channels_cfg.matrix.enabled:
        task = asyncio.create_task(MatrixGateway(channels_cfg.matrix, gw).run(), name="matrix")
        pairs.append((task, "matrix"))
    if channels_cfg.whatsapp.enabled:
        task = asyncio.create_task(
            WhatsAppGateway(channels_cfg.whatsapp, gw).run(), name="whatsapp"
        )
        pairs.append((task, "whatsapp"))
    return pairs


async def _run_gateway(
    settings: Settings,
    *,
    persona_config: Any | None = None,
    profile: RavnProfile | None = None,
) -> None:
    """Build and run the gateway until interrupted."""
    from ravn.adapters.channels.gateway import RavnGateway
    from ravn.adapters.channels.gateway_http import HttpGateway
    from ravn.adapters.channels.gateway_telegram import TelegramGateway
    from ravn.ports.channel import ChannelPort

    if profile is not None:
        system_prompt, max_iterations, max_tokens_gw = _apply_profile(
            profile, settings, persona_config=persona_config
        )
    else:
        system_prompt = settings.agent.system_prompt
        max_iterations = settings.agent.max_iterations
        max_tokens_gw = settings.effective_max_tokens()
        if persona_config is not None:
            if persona_config.system_prompt_template:
                system_prompt = persona_config.system_prompt_template
            if persona_config.iteration_budget is not None:
                max_iterations = persona_config.iteration_budget
    resolved_model = _resolve_persona_model(settings, persona_config)

    # Shared resources (safe to reuse across sessions)
    workspace = _resolve_workspace(settings)
    cli_transport_executor = _uses_cli_transport_executor(persona_config)
    llm = None if cli_transport_executor else _build_llm(settings)
    memory = _build_memory(settings, llm=llm)
    compressor = None if cli_transport_executor else _build_compressor(settings, llm)
    prompt_builder = _build_prompt_builder(settings)
    pre_hooks, post_hooks = _build_hooks(settings)

    extended_thinking = (
        settings.llm.extended_thinking
        if settings.llm.extended_thinking.enabled and not cli_transport_executor
        else None
    )

    # Start MCP servers (shared across sessions)
    mcp_manager: Any | None = None
    mcp_tools: list[Any] = []

    def _agent_factory(channel: ChannelPort) -> ExecutionAgentPort:
        # Per-session: fresh session, budget, and tools
        session = Session()
        budget = _build_iteration_budget(settings, max_iterations)
        permission_mode = settings.permission.mode
        if persona_config is not None and persona_config.permission_mode:
            permission_mode = persona_config.permission_mode
        permission = _build_permission(
            settings,
            workspace,
            no_tools=False,
            persona_config=persona_config,
        )
        tools = _build_tools(
            settings,
            workspace,
            session,
            llm,
            memory,
            budget,
            persona_config=persona_config,
            permission=permission,
        )
        # Append shared MCP tools to per-session tool list
        tools.extend(mcp_tools)

        # Wrap with Sleipnir broadcast when enabled
        effective_channel: ChannelPort = channel
        if settings.sleipnir.enabled:
            from ravn.adapters.channels.composite import CompositeChannel
            from ravn.adapters.channels.sleipnir import SleipnirChannel

            sleipnir_ch = SleipnirChannel(
                settings.sleipnir,
                session_id=str(session.id),
                task_id=None,
            )
            effective_channel = CompositeChannel([channel, sleipnir_ch])

        executor = _build_executor(persona_config)
        return executor.build(
            llm=llm,
            tools=tools,
            channel=effective_channel,
            permission=permission,
            permission_mode=permission_mode,
            system_prompt=system_prompt,
            model=resolved_model,
            max_tokens=max_tokens_gw,
            max_iterations=max_iterations,
            workspace_dir=str(workspace),
            mcp_servers=_transport_mcp_servers(settings),
            session=session,
            pre_tool_hooks=pre_hooks or None,
            post_tool_hooks=post_hooks or None,
            user_input_fn=None,  # Gateway has no stdin
            memory=memory,
            episode_summary_max_chars=settings.agent.episode_summary_max_chars,
            episode_task_max_chars=settings.agent.episode_task_max_chars,
            iteration_budget=budget,
            compressor=compressor,
            prompt_builder=prompt_builder,
            reflection_model=settings.effective_memory_reflection_model(),
            reflection_max_tokens=settings.memory.reflection_max_tokens,
            task_summary_max_chars=settings.memory.task_summary_max_chars,
            input_token_cost_per_million=settings.memory.input_token_cost_per_million,
            output_token_cost_per_million=settings.memory.output_token_cost_per_million,
            extended_thinking=extended_thinking,
            max_prompt_tokens=settings.context_management.max_prompt_tokens,
            max_tool_result_chars=settings.tools.max_result_chars,
        )

    gw = RavnGateway(settings.gateway, _agent_factory, profile=profile)

    tasks: list[asyncio.Task] = []

    if settings.gateway.channels.telegram.enabled:
        tg = TelegramGateway(settings.gateway.channels.telegram, gw)
        tasks.append(asyncio.create_task(tg.run(), name="telegram"))

    if settings.gateway.channels.http.enabled:
        ht = HttpGateway(settings.gateway.channels.http, gw)
        tasks.append(asyncio.create_task(ht.run(), name="http"))

    for task, _ in _make_channel_tasks(settings.gateway.channels, gw):
        tasks.append(task)

    typer.echo(f"Gateway started ({len(tasks)} channel(s) active). Press Ctrl+C to stop.")

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
        await _shutdown_mcp(mcp_manager)


@app.command()
def daemon(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    persona: str = typer.Option(
        "", "--persona", "-p", help="Persona name applied to all daemon sessions."
    ),
    profile: str = typer.Option(
        "", "--profile", help="Profile name (built-in or from ~/.ravn/profiles/)."
    ),
    resume: bool = typer.Option(
        True,
        "--resume/--no-resume",
        help="Resume unfinished tasks from the journal.",
    ),
) -> None:
    """Start gateway channels AND drive loop simultaneously.  Never exits.

    Channels and triggers are configured via the ``gateway:`` and
    ``initiative:`` sections of ravn.yaml.  The daemon runs until Ctrl+C
    or SIGTERM.
    """
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)
    _log_effective_config(settings)
    project_config = ProjectConfig.discover()
    ravn_profile = _require_profile(profile)
    effective_persona = persona or (ravn_profile.persona if ravn_profile else "")
    persona_config = _require_persona(effective_persona, project_config, settings=settings)

    asyncio.run(
        _run_daemon(settings, persona_config=persona_config, profile=ravn_profile, resume=resume)
    )


@app.command()
def listen(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    persona: str = typer.Option(
        "", "--persona", "-p", help="Default persona for dispatched tasks."
    ),
    profile: str = typer.Option(
        "", "--profile", help="Profile name (built-in or from ~/.ravn/profiles/)."
    ),
) -> None:
    """Listen for remotely dispatched tasks via Sleipnir (NIU-505).

    Subscribes to ``ravn.task.dispatch`` on the configured RabbitMQ exchange
    and executes each incoming task autonomously.  Requires Sleipnir to be
    enabled: set ``sleipnir.enabled: true`` in ravn.yaml and provide the
    ``SLEIPNIR_AMQP_URL`` environment variable.

    The persona requested in each dispatch event is validated; tasks with
    unknown personas are rejected and a ``ravn.task.rejected`` event is
    published back to the exchange.
    """
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)
    _log_effective_config(settings)
    project_config = ProjectConfig.discover()
    ravn_profile = _require_profile(profile)
    effective_persona = persona or (ravn_profile.persona if ravn_profile else "")
    persona_config = _require_persona(effective_persona, project_config, settings=settings)

    asyncio.run(
        _run_daemon(
            settings,
            persona_config=persona_config,
            profile=ravn_profile,
            task_dispatch=True,
        )
    )


@app.command()
def peers(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show address, latency, task_count, last_seen."
    ),
    scan: bool = typer.Option(
        False, "--scan", help="Force a fresh mDNS/K8s scan before displaying."
    ),
) -> None:
    """List verified flock members with persona, capabilities, and status.

    \b
    Examples:
      ravn peers               — list verified peers
      ravn peers --verbose     — include address, latency, task_count
      ravn peers --scan        — force a fresh network scan first
    """
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)
    asyncio.run(_run_peers(settings, verbose=verbose, force_scan=scan))


# Focused runtime modules. The synchronized compatibility wrappers preserve
# the long-standing ravn.cli.commands import and monkeypatch surface.
from functools import wraps as _wraps  # noqa: E402

from ravn.cli import daemon_runtime as _daemon_runtime  # noqa: E402
from ravn.cli import mesh_runtime as _mesh_runtime  # noqa: E402
from ravn.cli import resident_runtime_wiring as _resident_runtime_wiring  # noqa: E402
from ravn.cli import trigger_wiring as _trigger_wiring  # noqa: E402

_RUNTIME_WRAPPERS: dict[str, Any] = {}


def _sync_runtime_module(module: Any, own_names: frozenset[str]) -> None:
    for name, value in globals().items():
        if name.startswith("__"):
            continue
        if name in own_names and _RUNTIME_WRAPPERS.get(name) is value:
            continue
        module.__dict__[name] = value


def _runtime_wrapper(module: Any, name: str, own_names: frozenset[str]) -> Any:
    implementation = getattr(module, name)
    if inspect.iscoroutinefunction(implementation):

        @_wraps(implementation)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            _sync_runtime_module(module, own_names)
            return await implementation(*args, **kwargs)

        return async_wrapper

    @_wraps(implementation)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        _sync_runtime_module(module, own_names)
        return implementation(*args, **kwargs)

    return wrapper


_DAEMON_RUNTIME_NAMES = frozenset(("_run_daemon",))
_run_daemon = _runtime_wrapper(_daemon_runtime, "_run_daemon", _DAEMON_RUNTIME_NAMES)

_RESIDENT_RUNTIME_WIRING_NAMES = frozenset(
    (
        "_build_environment_signal_runtime",
        "_build_resident_inbox",
        "_build_resident_state",
        "_build_resident_runtime",
        "_build_resident_learning_runtime",
        "_build_realm_capability_sync",
        "_build_resident_wakefulness",
        "_build_odin_court",
        "_run_odin_court_sweep",
        "_build_feedback_recorder",
        "_build_evolution_adapter",
        "_build_environment_signal_publisher",
        "_wire_triggers",
    )
)
_build_resident_inbox = _runtime_wrapper(
    _resident_runtime_wiring, "_build_resident_inbox", _RESIDENT_RUNTIME_WIRING_NAMES
)
_build_resident_state = _runtime_wrapper(
    _resident_runtime_wiring, "_build_resident_state", _RESIDENT_RUNTIME_WIRING_NAMES
)
_build_resident_runtime = _runtime_wrapper(
    _resident_runtime_wiring, "_build_resident_runtime", _RESIDENT_RUNTIME_WIRING_NAMES
)
_build_environment_signal_runtime = _runtime_wrapper(
    _resident_runtime_wiring, "_build_environment_signal_runtime", _RESIDENT_RUNTIME_WIRING_NAMES
)
_build_resident_learning_runtime = _runtime_wrapper(
    _resident_runtime_wiring, "_build_resident_learning_runtime", _RESIDENT_RUNTIME_WIRING_NAMES
)
_build_realm_capability_sync = _runtime_wrapper(
    _resident_runtime_wiring, "_build_realm_capability_sync", _RESIDENT_RUNTIME_WIRING_NAMES
)
_build_resident_wakefulness = _runtime_wrapper(
    _resident_runtime_wiring, "_build_resident_wakefulness", _RESIDENT_RUNTIME_WIRING_NAMES
)
_build_odin_court = _runtime_wrapper(
    _resident_runtime_wiring, "_build_odin_court", _RESIDENT_RUNTIME_WIRING_NAMES
)
_run_odin_court_sweep = _runtime_wrapper(
    _resident_runtime_wiring, "_run_odin_court_sweep", _RESIDENT_RUNTIME_WIRING_NAMES
)
_build_feedback_recorder = _runtime_wrapper(
    _resident_runtime_wiring, "_build_feedback_recorder", _RESIDENT_RUNTIME_WIRING_NAMES
)
_build_evolution_adapter = _runtime_wrapper(
    _resident_runtime_wiring, "_build_evolution_adapter", _RESIDENT_RUNTIME_WIRING_NAMES
)
_build_environment_signal_publisher = _runtime_wrapper(
    _resident_runtime_wiring, "_build_environment_signal_publisher", _RESIDENT_RUNTIME_WIRING_NAMES
)
_wire_triggers = _runtime_wrapper(
    _resident_runtime_wiring, "_wire_triggers", _RESIDENT_RUNTIME_WIRING_NAMES
)

_TRIGGER_WIRING_NAMES = frozenset(
    (
        "_wire_mimir_triggers",
        "_wire_cron",
        "_wire_task_dispatch",
        "_derive_capabilities",
        "_wire_cascade",
    )
)
_wire_mimir_triggers = _runtime_wrapper(
    _trigger_wiring, "_wire_mimir_triggers", _TRIGGER_WIRING_NAMES
)
_wire_cron = _runtime_wrapper(_trigger_wiring, "_wire_cron", _TRIGGER_WIRING_NAMES)
_wire_task_dispatch = _runtime_wrapper(
    _trigger_wiring, "_wire_task_dispatch", _TRIGGER_WIRING_NAMES
)
_derive_capabilities = _runtime_wrapper(
    _trigger_wiring, "_derive_capabilities", _TRIGGER_WIRING_NAMES
)
_wire_cascade = _runtime_wrapper(_trigger_wiring, "_wire_cascade", _TRIGGER_WIRING_NAMES)

_MESH_RUNTIME_NAMES = frozenset(
    (
        "_build_mesh",
        "_resolve_transport_kwargs",
        "_build_discovery",
        "_run_peers",
    )
)
_build_mesh = _runtime_wrapper(_mesh_runtime, "_build_mesh", _MESH_RUNTIME_NAMES)
_resolve_transport_kwargs = _runtime_wrapper(
    _mesh_runtime, "_resolve_transport_kwargs", _MESH_RUNTIME_NAMES
)
_build_discovery = _runtime_wrapper(_mesh_runtime, "_build_discovery", _MESH_RUNTIME_NAMES)
_run_peers = _runtime_wrapper(_mesh_runtime, "_run_peers", _MESH_RUNTIME_NAMES)

_RUNTIME_WRAPPERS.update(
    {
        name: globals()[name]
        for names in (
            _DAEMON_RUNTIME_NAMES,
            _RESIDENT_RUNTIME_WIRING_NAMES,
            _TRIGGER_WIRING_NAMES,
            _MESH_RUNTIME_NAMES,
        )
        for name in names
    }
)


@app.command()
def tui(
    connect: list[str] = typer.Option(
        [],
        "--connect",
        "-C",
        help="Connect to a Ravn daemon at host:port. May be repeated.",
    ),
    discover: bool = typer.Option(
        False,
        "--discover",
        help="Auto-discover Ravn daemons via mDNS.",
    ),
    layout: str = typer.Option(
        "",
        "--layout",
        "-l",
        help="Start with a named layout preset (flokk, cascade, mimir, compare, broadcast).",
    ),
    config: str = typer.Option(
        "",
        "--config",
        "-c",
        help="Path to ravn config YAML.",
    ),
) -> None:
    """Launch the Ravn TUI — terminal operator interface for Flokk management.

    \b
    Examples:
      ravn tui                                   — auto-discover via mDNS
      ravn tui --connect tanngrisnir.gimle:7477  — explicit target
      ravn tui --connect t1:7477 --connect t2:7477 --connect t3:7477
      ravn tui --layout cascade
    """
    if config:
        os.environ["RAVN_CONFIG"] = config

    try:
        from ravn.tui.app import RavnTUI
    except ImportError as exc:
        typer.echo(
            f"Textual is required for the TUI: pip install ravn[tui]\n{exc}",
            err=True,
        )
        raise typer.Exit(1) from exc

    parsed_connections: list[tuple[str, int]] = []
    for spec in connect:
        if ":" not in spec:
            typer.echo(f"Invalid --connect value {spec!r} (expected host:port)", err=True)
            raise typer.Exit(1)
        host, port_str = spec.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            typer.echo(f"Invalid port in {spec!r}", err=True)
            raise typer.Exit(1)
        parsed_connections.append((host, port))

    # Extract mimir HTTP instance URLs from the loaded config
    mimir_urls: list[tuple[str, str]] = []
    with suppress(Exception):
        from ravn.config import Settings

        settings = Settings()
        for inst in sorted(settings.mimir.instances, key=lambda i: i.read_priority):
            if inst.url:
                mimir_urls.append((inst.name, inst.url))

    ravn_tui = RavnTUI(
        connections=parsed_connections,
        discover=discover or not parsed_connections,
        layout_name=layout or None,
        mimir_urls=mimir_urls,
    )
    ravn_tui.run()


@app.command()
def web(
    port: int = typer.Option(7477, "--port", "-p", help="Port to listen on (default: 7477)."),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address (default: 0.0.0.0)."),
    persona_dirs: list[str] = typer.Option(
        [],
        "--persona-dir",
        help="Extra directory to search for persona YAML files. May be repeated.",
    ),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (development only)."),
) -> None:
    """Start the standalone Ravn web UI with persona management.

    Spins up a lightweight FastAPI + web UI server — no Volundr, Ting, or
    PostgreSQL required.  Personas are loaded from the filesystem.

    \b
    Examples:
      ravn web                      — start on http://0.0.0.0:7477
      ravn web --port 8080          — use a custom port
      ravn web --persona-dir ./my-personas
    """
    from ravn.web import serve

    serve(
        host=host,
        port=port,
        persona_dirs=persona_dirs if persona_dirs else None,
        reload=reload,
    )


def main() -> None:
    app()


def gateway_main() -> None:
    gateway_app()


def evolve_main() -> None:
    evolve_app()


# ---------------------------------------------------------------------------
# Tool-build doctor CLI
# ---------------------------------------------------------------------------

tool_build_app = typer.Typer(
    name="tool-build",
    help="Diagnose the Valkyrie tool-build path (Ting/Forge).",
    add_completion=False,
)
app.add_typer(tool_build_app, name="tool-build")


def _render_doctor_report(report: Any) -> None:
    """Print the doctor checklist as human-readable PASS/FAIL/SKIP lines."""
    for hop in report.hops:
        typer.echo(f"Hop {hop.number} — {hop.title}: {hop.status.value} — {hop.reason}")


@tool_build_app.command("doctor")
def tool_build_doctor(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the checklist as structured JSON."
    ),
) -> None:
    """Diagnose the Valkyrie -> Ting/Forge tool-build path hop by hop.

    Runs READ-ONLY probes only — it never launches an actual build. Each hop
    reports PASS/FAIL/SKIP with a one-line reason so a misconfiguration
    surfaces precisely instead of as a vague ToolBuildError inside a poll loop.

    Exits non-zero when any hop fails.
    """
    from ravn.valkyrie_evolution.tool_build_doctor import diagnose_tool_build  # noqa: PLC0415

    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    report = asyncio.run(diagnose_tool_build(settings))

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        _render_doctor_report(report)

    if not report.ok:
        raise typer.Exit(1)


_TING_WORKFLOWS_PATH = "/api/v1/ting/workflows"


def _effective_workflow_selector(settings: Settings) -> dict[str, Any] | None:
    """Effective tool-builder selector: realm grant overrides static config."""
    realm_config = _resolve_realm_build_config(settings)
    if realm_config.workflow_selector is not None:
        return realm_config.workflow_selector
    static = settings.resident_evolution.tool_builder_workflow
    if static.names or static.tags:
        return static.model_dump()
    return None


async def _discover_ting_workflows(backend: Any) -> tuple[list[Any], str]:
    """List workflows via the backend's client. Returns (workflows, error)."""
    from ravn.adapters.tool_build.ting_workflow import _workflow_from_body  # noqa: PLC0415

    client = getattr(backend, "client", None)
    base_url = getattr(backend, "base_url", "")
    if client is None or not base_url:
        return [], "configured backend exposes no client/base_url to query"
    url = f"{base_url}{_TING_WORKFLOWS_PATH}"
    resp = await client.get(url)
    if resp.status_code != 200 or not isinstance(resp.body, list):
        return [], f"GET {url} -> HTTP {resp.status_code} (expected a workflow list)"
    workflows = [_workflow_from_body(item) for item in resp.body if isinstance(item, dict)]
    return workflows, ""


@tool_build_app.command("workflows")
def tool_build_workflows(
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
    json_output: bool = typer.Option(False, "--json", help="Emit workflows as structured JSON."),
) -> None:
    """List Ting workflows and mark those matching the tool-build selector.

    READ-ONLY inspection of the "configure from existing Ting workflows" UX.
    The selector is the realm build grant's workflow (when a realm is
    configured and grants one) or the static ``tool_builder_workflow``.
    """
    from ravn.domain.capability_catalog import WorkflowSelector  # noqa: PLC0415

    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    backend = _build_tool_build_backend(settings)
    if backend is None:
        typer.echo("No tool-build backend configured (inline authoring).", err=True)
        raise typer.Exit(1)

    try:
        workflows, error = asyncio.run(_discover_ting_workflows(backend))
    except Exception as exc:  # noqa: BLE001 — inspection must never throw a traceback
        typer.echo(f"Failed to list workflows: {exc}", err=True)
        raise typer.Exit(1) from exc

    if error:
        typer.echo(error, err=True)
        raise typer.Exit(1)

    selector_dict = _effective_workflow_selector(settings)
    selector = (
        WorkflowSelector(**selector_dict) if selector_dict is not None else WorkflowSelector()
    )
    rows = [
        {
            "id": wf.workflow_id,
            "name": wf.name,
            "tags": list(wf.tags),
            "matches_selector": selector.configured and selector.matches(wf),
        }
        for wf in workflows
    ]

    if json_output:
        typer.echo(json.dumps({"selector": selector_dict, "workflows": rows}, indent=2))
        return

    if not rows:
        typer.echo("No workflows discovered.")
        return

    for row in rows:
        marker = "*" if row["matches_selector"] else " "
        tags = ", ".join(row["tags"]) if row["tags"] else "-"
        typer.echo(f"{marker} {row['name'] or row['id']}  id={row['id']}  tags={tags}")


# ---------------------------------------------------------------------------
# Mímir CLI
# ---------------------------------------------------------------------------

mimir_app = typer.Typer(
    name="mimir",
    help="Mímir knowledge-base utilities.",
    add_completion=False,
)
app.add_typer(mimir_app, name="mimir")


@mimir_app.command("ingest")
def mimir_ingest_cmd(
    path: str = typer.Argument(..., help="Path to a file to ingest, or '-' to read from stdin."),
    title: str = typer.Option("", "--title", "-t", help="Title override (defaults to filename)."),
    source_type: str = typer.Option(
        "document",
        "--type",
        help="Source type: document, web, research, conversation, tool_output.",
    ),
    origin_url: str = typer.Option("", "--url", "-u", help="Original URL (optional metadata)."),
    target: str = typer.Option(
        "",
        "--mimir",
        "-m",
        help=(
            "Named Mímir instance to ingest into (e.g. 'local', 'shared'). "
            "Defaults to all configured instances."
        ),
    ),
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
) -> None:
    """Ingest a file or stdin into the Mímir knowledge base.

    Works with both local (filesystem) and remote (HTTP) Mímir adapters —
    the adapter is selected from ravn config, no explicit URL needed.

    Examples::

        ravn mimir ingest ./architecture.md
        ravn mimir ingest ./notes.txt --title "Sprint retro notes" --type research
        ravn mimir ingest ./doc.md --mimir local
        ravn mimir ingest ./doc.md --mimir shared
        cat doc.md | ravn mimir ingest -
    """
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)

    if not settings.mimir.enabled:
        typer.echo("Mímir is disabled in config (mimir.enabled = false).", err=True)
        raise typer.Exit(1)

    asyncio.run(
        _run_mimir_ingest(settings, path, title, source_type, origin_url or None, target or None)
    )


def _build_single_mimir(settings: Settings, name: str) -> Any:
    """Build a single named Mímir adapter by instance name.

    Searches ``settings.mimir.instances`` for *name*.  Falls back to the
    single-path local adapter when instances are not configured and *name*
    is ``'local'``.
    """
    for inst in settings.mimir.instances:
        if inst.name != name:
            continue
        if inst.path:
            from mimir.adapters.markdown import MarkdownMimirAdapter

            return MarkdownMimirAdapter(root=inst.path)
        if inst.url:
            from ravn.adapters.mimir.http import HttpMimirAdapter

            auth = None
            if inst.auth is not None:
                auth = _build_mimir_auth(settings, inst.auth)
            return HttpMimirAdapter(base_url=inst.url, auth=auth)

    # No instances configured — accept "local" as alias for the single path adapter
    if not settings.mimir.instances and name == "local":
        from mimir.adapters.markdown import MarkdownMimirAdapter

        return MarkdownMimirAdapter(root=settings.mimir.path)

    available = [inst.name for inst in settings.mimir.instances] or ["local"]
    typer.echo(f"Unknown Mímir instance {name!r}. Available: {', '.join(available)}", err=True)
    raise typer.Exit(1)


async def _run_mimir_ingest(
    settings: Settings,
    path: str,
    title: str,
    source_type: str,
    origin_url: str | None,
    target: str | None = None,
) -> None:
    import sys

    from niuu.domain.mimir import MimirSource, compute_content_hash

    mimir = _build_single_mimir(settings, target) if target else _build_mimir(settings)
    if mimir is None:
        typer.echo("Failed to build Mímir adapter — check config.", err=True)
        raise typer.Exit(1)

    if path == "-":
        content = sys.stdin.read()
        resolved_title = title or "stdin"
        resolved_path = "stdin"
    else:
        file_path = Path(path).expanduser()
        if not file_path.exists():
            typer.echo(f"File not found: {file_path}", err=True)
            raise typer.Exit(1)
        content = file_path.read_text(encoding="utf-8", errors="replace")
        resolved_title = title or file_path.stem.replace("-", " ").replace("_", " ").title()
        resolved_path = str(file_path)

    if not content.strip():
        typer.echo("Content is empty — nothing to ingest.", err=True)
        raise typer.Exit(1)

    content_hash = compute_content_hash(content)
    source_id = "src_" + content_hash[:16]

    source = MimirSource(
        source_id=source_id,
        title=resolved_title,
        content=content,
        source_type=source_type,  # type: ignore[arg-type]
        origin_url=origin_url,
        content_hash=content_hash,
        ingested_at=datetime.now(UTC),
    )

    await mimir.ingest(source)
    typer.echo(f"Ingested: {resolved_title!r}")
    typer.echo(f"source_id: {source_id}")
    typer.echo(f"file:      {resolved_path}")
    typer.echo(f"target:    {target or 'all'}")
    typer.echo("Synthesis will be triggered automatically by the daemon (or run ravn chat).")


def mimir_main() -> None:
    mimir_app()
