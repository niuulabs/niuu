"""Ravn CLI entry point."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import os
import signal
import sys
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
import yaml

from ravn.agent import PostToolHook, PreToolHook
from ravn.config import (
    InitiativeConfig,
    ProjectConfig,
    Settings,
    ToolGroupConfig,
    resolve_trust_tools,
)
from ravn.domain.checkpoint import InterruptReason
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.models import (
    AgentTask,
    Message,
    OutputMode,
    Session,
    TodoItem,
    TodoStatus,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from ravn.domain.profile import RavnProfile
from ravn.ports.checkpoint import CheckpointPort
from ravn.ports.executor import ExecutionAgentPort, ExecutorPort

logger = logging.getLogger(__name__)

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

warden_app = typer.Typer(
    name="warden",
    help="Manage persisted long-lived Ravn wardens.",
    add_completion=False,
)
app.add_typer(warden_app, name="warden")

from ravn.cli.flock import flock_app  # noqa: E402 — must be after app is defined

app.add_typer(flock_app, name="flock")


def approvals_main() -> None:
    approvals_app()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _import_class(dotted_path: str) -> type:
    """Dynamically import a class from a fully-qualified dotted path."""

    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _inject_secrets(kwargs: dict[str, Any], secret_map: dict[str, str]) -> dict[str, Any]:
    """Resolve env var names from *secret_map* and merge into *kwargs*."""
    merged = dict(kwargs)
    for kwarg_name, env_var in secret_map.items():
        value = os.environ.get(env_var, "")
        if value:
            merged[kwarg_name] = value
    return merged


def _resolve_workspace(settings: Settings) -> Path:
    """Return the workspace root from config, defaulting to cwd."""
    ws = settings.permission.workspace_root
    return Path(ws).resolve() if ws else Path.cwd()


def _resident_ravn_state_dir(workspace: Path) -> Path:
    """Return the writable resident Ravn state directory for skills/metadata."""
    override = os.environ.get("RAVN_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if os.access(workspace, os.W_OK):
        return workspace / ".ravn"
    return Path.home() / ".ravn"


def _attach_signal_build_tool(
    agent: Any,
    workspace: Path,
    *,
    triggered_by: str | None,
    settings: Settings,
    publisher: Any | None = None,
    drive_loop: Any | None = None,
) -> Any:
    """Attach build_tool to RavnAgent signal investigations when supported."""
    if not triggered_by or not triggered_by.startswith("signal:"):
        return agent

    def _investigation_context() -> str:
        # Lazily read the running task's prompt at build time — the drive loop
        # sets the current-task contextvar while the session executes, so a
        # filed review carries the exact ticket the resident was working.
        task = drive_loop.current_task() if drive_loop is not None else None
        return getattr(task, "initiative_context", "") if task is not None else ""

    try:
        from ravn.adapters.tools.build_tool import attach_build_tool  # noqa: PLC0415
        from ravn.odin.review import JsonReviewStore, ReviewRequester  # noqa: PLC0415
        from ravn.valkyrie_evolution.learned_tools import learned_tool_storage  # noqa: PLC0415

        state_dir = _resident_ravn_state_dir(workspace)
        valkyrie_id = settings.mesh.own_peer_id or f"valkyrie:{settings.environment.id}"
        review_requester = (
            ReviewRequester(
                publisher=publisher,
                store=JsonReviewStore(state_dir / "review_outbox.json"),
                source=valkyrie_id,
            )
            if publisher is not None
            else None
        )
        code_dir, artifacts_dir = learned_tool_storage(state_dir)
        attach_build_tool(
            agent,
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            publisher=publisher,
            review_requester=review_requester,
            autonomy_mode=settings.resident_evolution.autonomy_mode,
            environment_id=settings.environment.id,
            valkyrie_id=valkyrie_id,
            flock_id=settings.environment.flocks[0] if settings.environment.flocks else "",
            domain=settings.environment.type,
            execution_backend=settings.resident_evolution.learned_tool_execution_backend,
            workspace_root=workspace,
            build_backend=_build_tool_build_backend(settings),
            investigation_context=_investigation_context,
        )
    except TypeError:
        logger.debug("build_tool not attached: executor does not support dynamic tools")
    except Exception as exc:
        logger.warning("Failed to attach build_tool to signal investigation: %s", exc)
    return agent


def _build_tool_build_backend(settings: Settings) -> Any | None:
    """Construct the configured tool build adapter, or None for inline authoring."""
    cfg = settings.resident_evolution
    if not cfg.tool_build_adapter:
        return None
    cls = _import_class(cfg.tool_build_adapter)
    kwargs = dict(cfg.tool_build_kwargs)
    selector = cfg.tool_builder_workflow
    if (selector.names or selector.tags) and _constructor_accepts_kwarg(cls, "workflow_selector"):
        kwargs.setdefault("workflow_selector", selector.model_dump())
    return cls(**kwargs)


def _constructor_accepts_kwarg(cls: type, name: str) -> bool:
    signature = inspect.signature(cls)
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == name:
            return True
    return False


def _warden_store():
    from ravn.warden import build_warden_store

    return build_warden_store()


def _parse_deployment_kwargs(values: list[str] | None) -> dict[str, object]:
    """Parse repeated ``key=value`` deployment options."""
    parsed: dict[str, object] = {}
    for item in values or []:
        key, sep, raw_value = item.partition("=")
        if not sep or not key.strip():
            msg = f"Invalid deployment arg {item!r}; expected key=value"
            raise typer.BadParameter(msg)
        parsed[key.strip()] = yaml.safe_load(raw_value)
    return parsed


def _configure_logging(settings: Settings) -> None:
    """Apply logging config from settings."""
    level = getattr(logging, settings.logging.level.upper(), logging.WARNING)
    fmt = (
        "%(asctime)s %(name)s %(levelname)s %(message)s"
        if settings.logging.format == "text"
        else "%(message)s"
    )
    logging.basicConfig(level=level, format=fmt, force=True)


def _log_effective_config(settings: Settings) -> None:
    """Emit an INFO log with the effective config for drift detection."""
    source = os.environ.get("RAVN_CONFIG", "defaults")
    persona = os.environ.get("RAVN_PERSONA", "default")
    llm_alias = settings.effective_model()
    thinking = settings.llm.extended_thinking.enabled
    budget = settings.llm.extended_thinking.budget_tokens
    logger.info(
        "ravn effective config: persona=%s llm_alias=%s thinking=%s budget=%d source=%s",
        persona,
        llm_alias,
        thinking,
        budget,
        source,
    )


@warden_app.command("list")
def warden_list(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """List persisted wardens."""
    store = _warden_store()
    wardens = store.list()
    payload = [warden.model_dump(mode="json") for warden in wardens]

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    if not wardens:
        typer.echo("No wardens found.")
        return

    for warden in wardens:
        mounts = ", ".join(warden.mimir.mount_names) if warden.mimir.mount_names else "none"
        typer.echo(
            f"{warden.id}  persona={warden.persona}  "
            f"write_mount={warden.mimir.write_mount or '-'}  mounts={mounts}"
        )


@warden_app.command("show")
def warden_show(
    warden_id: str = typer.Argument(help="Warden id."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Show one persisted warden."""
    store = _warden_store()
    warden = store.get(warden_id)
    if warden is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)

    payload = warden.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"id: {warden.id}")
    typer.echo(f"name: {warden.name}")
    typer.echo(f"persona: {warden.persona}")
    typer.echo(f"profile: {warden.profile or '-'}")
    typer.echo(f"deployment: {warden.deployment}")
    typer.echo(f"write_mount: {warden.mimir.write_mount or '-'}")
    typer.echo(
        "mounts: " + (", ".join(warden.mimir.mount_names) if warden.mimir.mount_names else "none")
    )
    typer.echo(f"autostart: {str(warden.autostart).lower()}")
    typer.echo(f"installed: {str(warden.supervisor.installed).lower()}")
    typer.echo(f"state: {warden.runtime.state}")
    if warden.supervisor.service_file:
        typer.echo(f"service_file: {warden.supervisor.service_file}")
    if warden.supervisor.config_file:
        typer.echo(f"config_file: {warden.supervisor.config_file}")


@warden_app.command("create")
def warden_create(
    name: str = typer.Argument(help="Human-friendly warden name."),
    persona: str = typer.Option(
        "research-and-distill",
        "--persona",
        help="Default persona used by this warden.",
    ),
    profile: str = typer.Option("", "--profile", help="Optional Ravn profile name."),
    deployment: str = typer.Option(
        "launchd",
        "--deployment",
        help=(
            "Deployment backend shorthand, for example launchd, systemd, k8s-apply, or k8s-gitops."
        ),
    ),
    deployment_arg: list[str] = typer.Option(
        None,
        "--deployment-arg",
        help="Repeat key=value pairs for deployment backend configuration.",
    ),
    mount: list[str] = typer.Option(
        None,
        "--mount",
        help="Repeat to attach one or more Mimir mounts.",
    ),
    write_mount: str = typer.Option("", "--write-mount", help="Default Mimir write mount."),
    autostart: bool = typer.Option(False, "--autostart", help="Mark this warden for autostart."),
    created_by: str = typer.Option("cli", "--created-by", help="Creator label for provenance."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Create and persist a new warden spec."""
    from ravn.warden import WardenSpec, resolve_deployment_adapter

    mounts = mount or []
    deployment_kwargs = _parse_deployment_kwargs(deployment_arg)
    spec = WardenSpec(
        id="",
        name=name,
        persona=persona,
        profile=profile,
        deployment=deployment,
        deployment_adapter=resolve_deployment_adapter(deployment),
        deployment_kwargs=deployment_kwargs,
        mimir={
            "mount_names": mounts,
            "write_mount": write_mount,
        },
        autostart=autostart,
        created_by=created_by,
    )

    store = _warden_store()
    created = store.create(spec)
    payload = created.model_dump(mode="json")

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Created warden {created.id}")
    typer.echo(f"  persona: {created.persona}")
    typer.echo(f"  write_mount: {created.mimir.write_mount or '-'}")
    typer.echo(
        "  mounts: "
        + (", ".join(created.mimir.mount_names) if created.mimir.mount_names else "none")
    )


@warden_app.command("install")
def warden_install(
    warden_id: str = typer.Argument(help="Warden id."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Generate local service artifacts for one warden."""
    from ravn.ports.warden_deployer import WardenDeploymentError

    store = _warden_store()
    try:
        installed = store.install(warden_id, workspace_root=Path.cwd())
    except WardenDeploymentError as exc:
        typer.echo(f"Failed to install warden {warden_id}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if installed is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)

    payload = installed.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Installed warden {installed.id}")
    typer.echo(f"  service: {installed.supervisor.service_label}")
    typer.echo(f"  service_file: {installed.supervisor.service_file}")
    typer.echo(f"  config_file: {installed.supervisor.config_file}")


@warden_app.command("start")
def warden_start(
    warden_id: str = typer.Argument(help="Warden id."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Mark an installed warden as started."""
    from ravn.ports.warden_deployer import WardenDeploymentError

    store = _warden_store()
    warden = store.get(warden_id)
    if warden is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)
    if not warden.supervisor.installed:
        typer.echo(f"Warden must be installed before it can be started: {warden_id}", err=True)
        raise typer.Exit(1)

    try:
        started = store.start(warden_id)
    except WardenDeploymentError as exc:
        typer.echo(f"Failed to start warden {warden_id}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if started is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)

    payload = started.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Started warden {started.id}")
    typer.echo(f"  state: {started.runtime.state}")


@warden_app.command("stop")
def warden_stop(
    warden_id: str = typer.Argument(help="Warden id."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Stop an installed warden."""
    from ravn.ports.warden_deployer import WardenDeploymentError

    store = _warden_store()
    warden = store.get(warden_id)
    if warden is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)
    if not warden.supervisor.installed:
        typer.echo(f"Warden must be installed before it can be stopped: {warden_id}", err=True)
        raise typer.Exit(1)

    try:
        stopped = store.stop(warden_id)
    except WardenDeploymentError as exc:
        typer.echo(f"Failed to stop warden {warden_id}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if stopped is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)

    payload = stopped.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Stopped warden {stopped.id}")
    typer.echo(f"  state: {stopped.runtime.state}")


@warden_app.command("uninstall")
def warden_uninstall(
    warden_id: str = typer.Argument(help="Warden id."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Uninstall a warden deployment while keeping the persisted spec."""
    from ravn.ports.warden_deployer import WardenDeploymentError

    store = _warden_store()
    try:
        uninstalled = store.uninstall(warden_id)
    except WardenDeploymentError as exc:
        typer.echo(f"Failed to uninstall warden {warden_id}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if uninstalled is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)

    payload = uninstalled.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Uninstalled warden {uninstalled.id}")
    typer.echo(f"  installed: {str(uninstalled.supervisor.installed).lower()}")
    typer.echo(f"  state: {uninstalled.runtime.state}")


# ---------------------------------------------------------------------------
# Builder: LLM
# ---------------------------------------------------------------------------


def _build_llm(settings: Settings) -> Any:
    """Build the LLM adapter (with optional fallback chain).

    The primary adapter is loaded dynamically from ``llm.provider.adapter``
    (defaults to ``AnthropicAdapter``).  Anthropic-specific defaults
    (``api_key``, ``base_url``) are injected automatically when the default
    adapter is used.
    """
    from ravn.ports.llm import LLMPort

    prov = settings.llm.provider
    cls = _import_class(prov.adapter)
    kwargs = _inject_secrets(dict(prov.kwargs), prov.secret_kwargs_env)

    kwargs.setdefault("model", settings.effective_model())
    kwargs.setdefault("max_tokens", settings.effective_max_tokens())
    kwargs.setdefault("max_retries", settings.llm.max_retries)
    kwargs.setdefault("retry_base_delay", settings.llm.retry_base_delay)
    kwargs.setdefault("timeout", settings.llm.timeout)

    primary: LLMPort = cls(**kwargs)

    if not settings.llm.fallbacks:
        return primary

    from ravn.adapters.llm.fallback import FallbackLLMAdapter

    providers: list[LLMPort] = [primary]
    for fb in settings.llm.fallbacks:
        fb_cls = _import_class(fb.adapter)
        fb_kwargs = _inject_secrets(dict(fb.kwargs), fb.secret_kwargs_env)
        providers.append(fb_cls(**fb_kwargs))

    return FallbackLLMAdapter(providers=providers)


def _build_executor(persona_config: Any | None = None) -> ExecutorPort:
    """Build the runtime-selected executor adapter.

    Persona-level runtime overrides win so mixed-provider workflows can run
    different transports in one flock. Session/workload transport settings are
    used as the default when a persona does not override them.
    """
    adapter_path = "ravn.adapters.executors.agent.AgentExecutor"
    kwargs: dict[str, Any] = {}

    executor_cfg = getattr(persona_config, "executor", None)
    if executor_cfg is not None and getattr(executor_cfg, "adapter", ""):
        adapter_path = str(executor_cfg.adapter)
        raw_kwargs = getattr(executor_cfg, "kwargs", {})
        if isinstance(raw_kwargs, dict):
            kwargs = dict(raw_kwargs)
    else:
        runtime_transport_adapter = str(os.environ.get("SKULD__TRANSPORT_ADAPTER") or "").strip()
        if runtime_transport_adapter:
            adapter_path = "ravn.adapters.executors.cli.CliTransportExecutor"
            kwargs = {"transport_adapter": runtime_transport_adapter}
            transport_kwargs = _runtime_cli_transport_kwargs(runtime_transport_adapter)
            if transport_kwargs:
                kwargs["transport_kwargs"] = transport_kwargs

    cls = _import_class(adapter_path)
    return cls(**kwargs)


def _runtime_cli_transport_kwargs(transport_adapter: str) -> dict[str, Any]:
    """Return explicit transport kwargs advertised by the runtime environment."""
    if transport_adapter != "skuld.transports.codex_ws.CodexWebSocketTransport":
        return {}

    kwargs: dict[str, Any] = {}
    skip_permissions = str(os.environ.get("SKULD__SKIP_PERMISSIONS") or "").strip()
    if skip_permissions:
        kwargs["skip_permissions"] = skip_permissions.lower() in {"1", "true", "yes", "on"}

    approval_policy = str(os.environ.get("SKULD__APPROVAL_POLICY") or "").strip()
    if approval_policy:
        kwargs["approval_policy"] = approval_policy

    sandbox = str(os.environ.get("SKULD__SANDBOX") or "").strip()
    if sandbox:
        kwargs["sandbox"] = sandbox

    return kwargs


def _transport_mcp_servers(settings: Settings) -> list[dict[str, Any]]:
    """Serialize enabled MCP server configs for CLI transports."""
    servers: list[dict[str, Any]] = []
    for server in settings.mcp_servers:
        if isinstance(server, dict):
            enabled = bool(server.get("enabled", True))
            if not enabled:
                continue
            transport = str(server.get("transport") or server.get("type") or "stdio")
            servers.append(
                {
                    "name": str(server.get("name") or ""),
                    "type": transport,
                    "command": str(server.get("command") or ""),
                    "args": list(server.get("args") or []),
                    "env": dict(server.get("env") or {}),
                    "url": str(server.get("url") or ""),
                }
            )
            continue
        servers.append(
            {
                "name": server.name,
                "type": server.transport,
                "command": server.command,
                "args": list(server.args),
                "env": dict(server.env),
                "url": server.url,
            }
        )
    return servers


def _uses_cli_transport_runtime() -> bool:
    """Return True when the daemon is configured to execute turns via CLI transport."""
    return bool(str(os.environ.get("SKULD__TRANSPORT_ADAPTER") or "").strip())


def _uses_cli_transport_executor(persona_config: Any | None = None) -> bool:
    """Return True when the effective executor is the CLI transport wrapper."""
    executor_cfg = getattr(persona_config, "executor", None)
    adapter_path = str(getattr(executor_cfg, "adapter", "") or "").strip()
    if adapter_path:
        return adapter_path == "ravn.adapters.executors.cli.CliTransportExecutor"
    return _uses_cli_transport_runtime()


# ---------------------------------------------------------------------------
# Builder: Memory + Embedding
# ---------------------------------------------------------------------------


def _build_memory(settings: Settings, llm: Any = None) -> Any:
    """Build the memory adapter (SQLite or Postgres), or None."""
    backend = settings.memory.backend

    if backend in {"", "none", "disabled", "off"}:
        return None

    embedding_port = None
    if settings.embedding.enabled:
        try:
            cls = _import_class(settings.embedding.adapter)
            kwargs = _inject_secrets(
                settings.embedding.kwargs,
                settings.embedding.secret_kwargs_env,
            )
            embedding_port = cls(**kwargs)
        except Exception as exc:
            logger.warning("Failed to load embedding adapter: %s — falling back to FTS-only", exc)

    adapter = None

    if backend == "sqlite":
        from ravn.adapters.memory.sqlite import SqliteMemoryAdapter

        adapter = SqliteMemoryAdapter(
            path=settings.memory.path,
            max_retries=settings.memory.max_retries,
            min_jitter_ms=settings.memory.min_retry_jitter_ms,
            max_jitter_ms=settings.memory.max_retry_jitter_ms,
            checkpoint_interval=settings.memory.checkpoint_interval,
            prefetch_budget=settings.memory.prefetch_budget,
            prefetch_limit=settings.memory.prefetch_limit,
            prefetch_min_relevance=settings.memory.prefetch_min_relevance,
            recency_half_life_days=settings.memory.recency_half_life_days,
            session_search_truncate_chars=settings.memory.session_search_truncate_chars,
            embedding_port=embedding_port,
            rrf_k=settings.embedding.rrf_k,
            semantic_candidate_limit=settings.embedding.semantic_candidate_limit,
        )

    elif backend == "postgres":
        from ravn.adapters.memory.postgres import PostgresMemoryAdapter

        dsn = os.environ.get(settings.memory.dsn_env, "") if settings.memory.dsn_env else ""
        dsn = dsn or settings.memory.dsn
        if not dsn:
            logger.warning(
                "Postgres memory backend configured but no DSN provided — memory disabled",
            )
            return None
        adapter = PostgresMemoryAdapter(dsn=dsn)

    else:
        # Custom backend via fully-qualified class path
        try:
            cls = _import_class(backend)
            adapter = cls(path=settings.memory.path)
        except Exception as exc:
            logger.warning("Failed to load custom memory backend %r: %s", backend, exc)
            return None

    return adapter


# ---------------------------------------------------------------------------
# Builder: Permission
# ---------------------------------------------------------------------------


def _build_permission(
    settings: Settings,
    workspace: Path,
    *,
    no_tools: bool,
    persona_config: Any | None,
) -> Any:
    """Build the permission adapter from config."""
    from ravn.adapters.permission.allow_deny import AllowAllPermission, DenyAllPermission

    if no_tools:
        return DenyAllPermission()

    # Determine effective permission mode: persona override takes precedence
    mode = settings.permission.mode
    if persona_config is not None and persona_config.permission_mode:
        mode = persona_config.permission_mode

    if mode in ("allow_all", "full_access"):
        return AllowAllPermission()

    if mode == "deny_all":
        return DenyAllPermission()

    # Rich permission enforcer for workspace_write, read_only, prompt modes
    from ravn.adapters.memory.approval import ApprovalMemory
    from ravn.adapters.permission.enforcer import PermissionEnforcer

    # Override config mode with the effective mode (persona takes precedence)
    effective_config = settings.permission.model_copy(update={"mode": mode})
    return PermissionEnforcer(
        config=effective_config,
        workspace_root=workspace,
        approval_memory=ApprovalMemory(project_root=workspace),
    )


# ---------------------------------------------------------------------------
# Builder: Tools — profile resolution and defaults
# ---------------------------------------------------------------------------

_DEFAULT_TOOL_GROUPS: dict[str, ToolGroupConfig] = {
    "default": ToolGroupConfig(
        include_groups=[
            "core",
            "extended",
            "skill",
            "platform",
            "cascade",
            "mimir",
            "workflow",
            "ravn",
        ],
        include_mcp=True,
    ),
    "worker": ToolGroupConfig(
        include_groups=["core"],
        include_mcp=False,
    ),
}


def _get_tool_group(settings: Settings, name: str) -> ToolGroupConfig:
    """Return the named tool group config, falling back to built-in defaults."""
    if name in settings.tools.profiles:
        return settings.tools.profiles[name]
    if name in _DEFAULT_TOOL_GROUPS:
        return _DEFAULT_TOOL_GROUPS[name]
    logger.warning("Unknown tool group %r — using 'default'", name)
    return _DEFAULT_TOOL_GROUPS["default"]


# ---------------------------------------------------------------------------
# Builder: Tools
# ---------------------------------------------------------------------------


def _resolve_mimir_auth_token(auth_config: Any) -> str | None:
    """Resolve a Mimir bearer token from config without storing secrets in YAML."""
    if auth_config is None:
        return None
    if auth_config.token:
        return auth_config.token
    token_env = getattr(auth_config, "token_env", None)
    if token_env:
        token = os.environ.get(str(token_env), "").strip()
        if token:
            return token
    token_file = getattr(auth_config, "token_file", None)
    if token_file:
        try:
            token = Path(str(token_file)).read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("Mímir auth token file is not readable: %s", token_file)
            return None
        if token:
            return token
    return None


def _build_mimir(settings: Settings) -> Any:
    """Build the Mímir adapter from config, or None if disabled."""
    if not settings.mimir.enabled:
        return None

    if settings.mimir.instances:
        from mimir.adapters.markdown import MarkdownMimirAdapter
        from ravn.adapters.mimir.composite import CompositeMimirAdapter
        from ravn.adapters.mimir.http import HttpMimirAdapter
        from ravn.domain.mimir import MimirAuth, MimirMount, WriteRouting

        mounts: list[Any] = []
        for inst in settings.mimir.instances:
            if inst.path:
                port: Any = MarkdownMimirAdapter(root=inst.path)
            elif inst.url:
                auth = None
                if inst.auth is not None:
                    auth = MimirAuth(
                        type=inst.auth.type,
                        token=_resolve_mimir_auth_token(inst.auth)
                        if inst.auth.type == "bearer"
                        else None,
                        token_file=inst.auth.token_file,
                        exchange_url=inst.auth.exchange_url,
                        audiences=tuple(inst.auth.audiences),
                        trust_domain=inst.auth.trust_domain,
                    )
                port = HttpMimirAdapter(base_url=inst.url, auth=auth)
            else:
                logger.warning("Mímir instance %r has neither path nor url — skipping", inst.name)
                continue
            mounts.append(
                MimirMount(
                    name=inst.name,
                    port=port,
                    role=inst.role,
                    read_priority=inst.read_priority,
                    categories=inst.categories,
                )
            )

        if not mounts:
            return None

        wr = settings.mimir.write_routing
        routing = WriteRouting(
            rules=[(r["prefix"], r["mounts"]) for r in wr.rules],
            default=wr.default,
        )
        return CompositeMimirAdapter(mounts=mounts, write_routing=routing)

    # Single local instance
    from mimir.adapters.markdown import MarkdownMimirAdapter

    return MarkdownMimirAdapter(root=settings.mimir.path)


def _build_workflow_capability_sources(settings: Settings) -> list[Any]:
    """Build enabled workflow capability adapters from environment config."""
    sources: list[Any] = []
    for source_cfg in settings.environment.capability_sources:
        if not source_cfg.enabled or not source_cfg.adapter:
            continue
        try:
            cls = _import_class(source_cfg.adapter)
            kwargs = _inject_secrets(
                dict(source_cfg.kwargs),
                source_cfg.secret_kwargs_env,
            )
            sources.append(cls(**kwargs))
        except Exception as exc:
            logger.warning(
                "Failed to load workflow capability source %r: %s",
                source_cfg.adapter,
                exc,
            )
    return sources


def _build_tools(
    settings: Settings,
    workspace: Path,
    session: Session,
    llm: Any,
    memory: Any | None,
    iteration_budget: Any | None,
    mimir: Any | None = None,
    *,
    no_tools: bool = False,
    persona_config: Any | None = None,
    profile: str = "default",
    discovery: Any | None = None,
    mimir_event_emitter: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
) -> list[Any]:
    """Build the tool list from the built-in registry, filtered by profile.

    The registry (``builtin_registry.BUILTIN_TOOLS``) drives all built-in
    tool construction.  Custom tools from ``settings.tools.custom`` are
    appended afterward.  MCP and cascade tools are NOT added here — callers
    are responsible for appending them based on the profile's ``include_mcp``
    flag and ``"cascade"`` group membership.
    """
    if no_tools:
        return []

    from ravn.adapters.tools.builtin_registry import BUILTIN_TOOLS
    from ravn.ports.tool import ToolPort

    profile_cfg = _get_tool_group(settings, profile)

    # When the persona declares explicit allowed_tools, derive include_groups
    # from those tool names so only the relevant groups are loaded.
    if persona_config is not None and getattr(persona_config, "allowed_tools", None):
        from ravn.config import ToolGroupConfig  # noqa: PLC0415

        profile_cfg = ToolGroupConfig(
            include_groups=_groups_for_persona(persona_config),
            include_mcp=profile_cfg.include_mcp,
        )

    include_groups = set(profile_cfg.include_groups)

    persona_prefix: str = (
        persona_config.system_prompt_template[:40]
        if persona_config and persona_config.system_prompt_template
        else ""
    )

    runtime_ctx: dict[str, Any] = {
        "workspace": workspace,
        "session": session,
        "llm": llm,
        "memory": memory,
        "iteration_budget": iteration_budget,
        "persona_prefix": persona_prefix,
        "discovery": discovery,
    }
    runtime_ctx["capability_tools_provider"] = lambda: runtime_ctx.get("capability_tools", [])

    # Pre-build shared skill port so both skill_list and skill_run reuse one instance
    if "skill" in include_groups and settings.skill.enabled:
        from ravn.adapters.tools.builtin_registry import _build_skill_port  # noqa: PLC0415

        runtime_ctx["skill_port"] = _build_skill_port(settings, workspace)

    if "workflow" in include_groups:
        workflow_sources = _build_workflow_capability_sources(settings)
        if workflow_sources:
            runtime_ctx["workflow_sources"] = workflow_sources

    tools: list[ToolPort] = []
    state_tool: Any = None

    for tool_key, tool_def in BUILTIN_TOOLS.items():
        if not (tool_def.groups & include_groups):
            continue
        if tool_def.condition is not None and not tool_def.condition(settings):
            continue
        if any(runtime_ctx.get(dep) is None for dep in tool_def.required_context):
            continue

        try:
            cls = _import_class(tool_def.adapter)
            kwargs = tool_def.kwargs_fn(settings, runtime_ctx)
            tool = cls(**kwargs)
            if tool_key == "ravn_state":
                state_tool = tool
            tools.append(tool)
        except Exception as exc:
            logger.warning("Failed to load built-in tool %r: %s", tool_key, exc)

    # -- Memory extra tools (dynamic, injected by the memory adapter) --
    if memory is not None:
        tools.extend(memory.extra_tools(session_id=str(session.id)))

    # -- Mímir tools (injected when adapter is wired and "mimir" group is active) --
    if mimir is not None and "mimir" in include_groups:
        from ravn.adapters.tools.entity_extractor import EntityExtractor
        from ravn.adapters.tools.mimir_tools import build_mimir_tools

        entity_extractor = None
        if settings.mimir.ingest.entity_detection and llm is not None:
            entity_extractor = EntityExtractor(mimir=mimir, llm=llm, config=settings.mimir.ingest)
        tools.extend(
            build_mimir_tools(
                mimir,
                workspace=workspace,
                entity_extractor=entity_extractor,
                event_emitter=mimir_event_emitter,
            )
        )

    # -- Custom tools from config --
    for ct in settings.tools.custom:
        try:
            cls = _import_class(ct.adapter)
            kwargs = _inject_secrets(ct.kwargs, ct.secret_kwargs_env)
            tools.append(cls(**kwargs))
        except Exception as exc:
            logger.warning("Failed to load custom tool %r: %s", ct.adapter, exc)

    tools.extend(_load_resident_learned_tools(settings, workspace, tools))

    # -- Apply enabled/disabled filters --
    tools = _filter_tools(tools, settings, persona_config)

    # Update state tool with final tool names after filtering
    runtime_ctx["capability_tools"] = list(tools)
    if state_tool is not None:
        state_tool._tool_names = [t.name for t in tools]

    return tools


def _load_resident_learned_tools(
    settings: Settings,
    workspace: Path,
    existing_tools: list[Any],
) -> list[Any]:
    """Load approved resident-authored learned tools from local state."""
    from ravn.valkyrie_evolution.learned_tools import (  # noqa: PLC0415
        ForgeSandboxLearnedToolRunner,
        learned_tool_storage,
        load_learned_tool,
        read_learned_tool_artifact,
    )

    state_dir = _resident_ravn_state_dir(workspace)
    backend = settings.resident_evolution.learned_tool_execution_backend
    if backend not in {"", "local", "forge", "devrunner"}:
        logger.warning("Skipping learned tools: unknown execution backend %s", backend)
        return []
    runner = (
        ForgeSandboxLearnedToolRunner(workspace_root=workspace)
        if backend in {"forge", "devrunner"}
        else None
    )

    code_dir, artifacts_dir = learned_tool_storage(state_dir)
    if not artifacts_dir.exists():
        return []
    seen = {tool.name for tool in existing_tools}
    loaded: list[Any] = []
    for artifact_file in sorted(artifacts_dir.glob("*.json")):
        try:
            artifact = read_learned_tool_artifact(artifact_file)
            if artifact.manifest.name in seen:
                continue
            tool_path = code_dir / f"{artifact.manifest.name}.py"
            if not tool_path.exists():
                continue
            tool = load_learned_tool(
                artifact=artifact,
                tool_path=tool_path,
                timeout_seconds=settings.resident_evolution.tool_timeout_seconds,
                runner=runner,
            )
        except Exception as exc:
            logger.warning("Failed to load learned tool artifact %s: %s", artifact_file, exc)
            continue
        seen.add(tool.name)
        loaded.append(tool)
    return loaded


def _in_groups(name: str, groups: set[str]) -> bool:
    """Return True if *name* matches any group prefix in *groups*.

    A match means either an exact hit (``name == group``) or a prefixed
    hit (``name`` starts with ``group_``).
    """
    return any(name == g or name.startswith(g + "_") for g in groups)


def _apply_trust_filter(
    tools: list[Any],
    settings: Settings,
    triggered_by: str | None,
) -> list[Any]:
    """Remove tools forbidden by the trust gradient for thread-triggered tasks."""
    if not triggered_by or not triggered_by.startswith("thread:"):
        return tools
    _allowed, forbidden = resolve_trust_tools(settings.trust)
    if not forbidden:
        return tools
    forbidden_set = set(forbidden)
    return [t for t in tools if not _in_groups(t.name, forbidden_set)]


def _filter_tools(
    tools: list[Any],
    settings: Settings,
    persona_config: Any | None,
) -> list[Any]:
    """Apply enabled/disabled and persona tool filters.

    ``persona_config.allowed_tools`` and ``forbidden_tools`` entries are treated
    as group aliases or prefixes (e.g. ``"file"`` expands to read_file, write_file,
    etc; ``"git"`` matches git_status, git_diff via prefix).
    ``settings.tools.enabled`` / ``disabled`` are exact tool names.
    """
    enabled_names = set(settings.tools.enabled)
    disabled_names = set(settings.tools.disabled)

    allowed_groups: set[str] = set()
    forbidden_groups: set[str] = set()
    if persona_config is not None:
        if persona_config.allowed_tools:
            allowed_groups = _expand_allowed_tools(set(persona_config.allowed_tools))
        if persona_config.forbidden_tools:
            forbidden_groups = set(persona_config.forbidden_tools)

    if allowed_groups or enabled_names:
        tools = [
            t
            for t in tools
            if t.name in enabled_names or (allowed_groups and _in_groups(t.name, allowed_groups))
        ]

    if disabled_names:
        tools = [t for t in tools if t.name not in disabled_names]

    if forbidden_groups:
        tools = [t for t in tools if not _in_groups(t.name, forbidden_groups)]

    return tools


# Maps documented group aliases to actual tool name prefixes.
# Needed because some groups don't use prefix_ naming (e.g. "file" → "read_file", not "file_read").
_MIMIR_TOOL_NAMES: list[str] = [
    "mimir_ingest",
    "mimir_query",
    "mimir_read",
    "mimir_read_source",
    "mimir_write",
    "mimir_publish_files",
    "mimir_search",
    "mimir_lint",
    "mimir_list",
]

_WORKFLOW_TOOL_NAMES: list[str] = [
    "workflow_list",
    "workflow_describe",
    "workflow_launch",
    "workflow_status",
    "workflow_events",
    "workflow_artifacts",
    "workflow_artifact_read",
]

_TOOL_GROUP_ALIASES: dict[str, list[str]] = {
    "file": ["read_file", "write_file", "edit_file", "glob_search", "grep_search"],
    "web": ["web_fetch", "web_search"],
    "terminal": ["terminal", "bash"],
    "mimir": _MIMIR_TOOL_NAMES,
    "workflow": _WORKFLOW_TOOL_NAMES,
    "cascade": ["cascade_delegate", "cascade_broadcast"],
    "volundr": ["volundr_session", "volundr_git"],
    "ravn": [
        "persona_validate",
        "persona_save",
        "skill_list",
        "skill_run",
        "skill_manage",
        "capability_list",
    ],
}


def _expand_allowed_tools(allowed: set[str]) -> set[str]:
    """Expand group aliases in allowed_tools to their constituent tool names."""
    expanded: set[str] = set()
    for item in allowed:
        if item in _TOOL_GROUP_ALIASES:
            expanded.update(_TOOL_GROUP_ALIASES[item])
        else:
            expanded.add(item)
    return expanded


def _groups_for_persona(persona_config: Any) -> list[str]:
    """Derive include_groups from a persona's allowed_tools.

    Reverse-maps each allowed tool name/prefix to the groups it belongs to in
    BUILTIN_TOOLS, so only the groups actually needed by the persona are loaded.
    ``core`` is always included as a baseline.
    """
    from ravn.adapters.tools.builtin_registry import BUILTIN_TOOLS  # noqa: PLC0415

    raw_allowed: set[str] = set(persona_config.allowed_tools or [])
    allowed: set[str] = _expand_allowed_tools(raw_allowed)
    forbidden: set[str] = _expand_allowed_tools(set(persona_config.forbidden_tools or []))

    groups: set[str] = {"core"}
    if allowed & set(_MIMIR_TOOL_NAMES):
        groups.add("mimir")
    if allowed & set(_WORKFLOW_TOOL_NAMES):
        groups.add("workflow")

    for key, tool_def in BUILTIN_TOOLS.items():
        # Use the same prefix-match logic as _filter_tools
        if any(key == a or key.startswith(a + "_") for a in allowed):
            if not any(key == f or key.startswith(f + "_") for f in forbidden):
                groups.update(tool_def.groups)

    return sorted(groups)


# ---------------------------------------------------------------------------
# Builder: Hooks
# ---------------------------------------------------------------------------


def _build_hooks(settings: Settings) -> tuple[list[PreToolHook], list[PostToolHook]]:
    """Build pre/post tool hook callables from config."""
    pre: list[PreToolHook] = []
    post: list[PostToolHook] = []

    for hc in settings.hooks.pre_tool:
        try:
            cls = _import_class(hc.adapter)
            kwargs = _inject_secrets(hc.kwargs, hc.secret_kwargs_env)
            hook_port = cls(**kwargs)

            async def _pre(tool_call: ToolCall, _hp: Any = hook_port) -> None:
                await _hp.pre_execute(tool_call.name, tool_call.input, {})

            pre.append(_pre)
        except Exception as exc:
            logger.warning("Failed to load pre-tool hook %r: %s", hc.adapter, exc)

    for hc in settings.hooks.post_tool:
        try:
            cls = _import_class(hc.adapter)
            kwargs = _inject_secrets(hc.kwargs, hc.secret_kwargs_env)
            hook_port = cls(**kwargs)

            async def _post(
                tool_call: ToolCall,
                result: ToolResult,
                _hp: Any = hook_port,
            ) -> None:
                await _hp.post_execute(tool_call.name, tool_call.input, result, {})

            post.append(_post)
        except Exception as exc:
            logger.warning("Failed to load post-tool hook %r: %s", hc.adapter, exc)

    return pre, post


# ---------------------------------------------------------------------------
# Builder: Compression & Prompt Builder
# ---------------------------------------------------------------------------


def _build_compressor(settings: Settings, llm: Any) -> Any:
    """Build the context compressor, or None."""
    from ravn.compression import ContextCompressor

    cm = settings.context_management
    return ContextCompressor(
        llm=llm,
        model=settings.effective_model(),
        max_tokens=cm.compression_max_tokens,
        protect_first=cm.protect_first_messages,
        protect_last=cm.effective_protect_last(),
        compression_threshold=cm.compression_threshold,
    )


def _build_prompt_builder(settings: Settings) -> Any:
    """Build the prompt builder with cache, or None."""
    from ravn.prompt_builder import PromptBuilder, PromptCache

    cm = settings.context_management
    cache = PromptCache(
        max_entries=cm.prompt_cache_max_entries,
        cache_dir=cm.prompt_cache_dir,
    )
    return PromptBuilder(cache=cache)


# ---------------------------------------------------------------------------
# Builder: MCP (async — called after agent construction)
# ---------------------------------------------------------------------------


async def _start_mcp(
    settings: Settings,
    agent: ExecutionAgentPort,
) -> Any | None:
    """Start MCP servers and register discovered tools into the agent.

    Returns the MCPManager instance (for shutdown), or None if no MCP
    servers are configured.
    """
    if not settings.mcp_servers:
        return None

    from ravn.adapters.mcp.auth import MCPAuthSession
    from ravn.adapters.mcp.manager import MCPManager
    from ravn.adapters.tools.mcp import MCPAuthTool

    # Build token store
    ts_cfg = settings.mcp_token_store
    if ts_cfg.backend == "openbao":
        from ravn.adapters.mcp.auth import OpenBaoTokenStore

        store = OpenBaoTokenStore(
            url=ts_cfg.openbao_url,
            token_env=ts_cfg.openbao_token_env,
            mount=ts_cfg.openbao_mount,
            path_prefix=ts_cfg.openbao_path_prefix,
        )
    else:
        from ravn.adapters.mcp.auth import LocalEncryptedTokenStore

        store = LocalEncryptedTokenStore(path=ts_cfg.local_path)

    auth_session = MCPAuthSession(store)
    builtin_names = set(agent._tools.keys())
    manager = MCPManager(settings.mcp_servers, builtin_tool_names=builtin_names)

    try:
        mcp_tools = await manager.start()
    except Exception as exc:
        logger.warning("MCP startup failed: %s — continuing without MCP tools", exc)
        return None

    # Register discovered tools into the agent
    for tool in mcp_tools:
        agent._tools[tool.name] = tool

    # Add the auth tool so the model can trigger auth flows
    server_configs = {s.name: s for s in settings.mcp_servers if s.enabled}
    auth_tool = MCPAuthTool(auth_session, server_configs, manager=manager)
    agent._tools[auth_tool.name] = auth_tool

    logger.info(
        "MCP started: %d server(s), %d tool(s) discovered",
        len(settings.mcp_servers),
        len(mcp_tools),
    )
    return manager


async def _start_mcp_shared(
    settings: Settings,
    *,
    tool_result_hook: Any | None = None,
) -> tuple[Any | None, list[Any]]:
    """Start MCP servers and return (manager, tools) for gateway use.

    Unlike :func:`_start_mcp`, this does not inject tools into an agent
    because the gateway creates agents per-session.  The returned tool
    list should be appended to each session's tool list.
    """
    if not settings.mcp_servers:
        return None, []

    from ravn.adapters.mcp.auth import MCPAuthSession
    from ravn.adapters.mcp.manager import MCPManager
    from ravn.adapters.tools.mcp import MCPAuthTool

    ts_cfg = settings.mcp_token_store
    if ts_cfg.backend == "openbao":
        from ravn.adapters.mcp.auth import OpenBaoTokenStore

        store = OpenBaoTokenStore(
            url=ts_cfg.openbao_url,
            token_env=ts_cfg.openbao_token_env,
            mount=ts_cfg.openbao_mount,
            path_prefix=ts_cfg.openbao_path_prefix,
        )
    else:
        from ravn.adapters.mcp.auth import LocalEncryptedTokenStore

        store = LocalEncryptedTokenStore(path=ts_cfg.local_path)

    auth_session = MCPAuthSession(store)
    manager = MCPManager(settings.mcp_servers, tool_result_hook=tool_result_hook)

    try:
        mcp_tools: list[Any] = await manager.start()
    except Exception as exc:
        logger.warning("MCP startup failed: %s — continuing without MCP tools", exc)
        return None, []

    server_configs = {s.name: s for s in settings.mcp_servers if s.enabled}
    auth_tool = MCPAuthTool(auth_session, server_configs, manager=manager)
    mcp_tools.append(auth_tool)

    logger.info(
        "MCP started: %d server(s), %d tool(s) discovered",
        len(settings.mcp_servers),
        len(mcp_tools) - 1,  # exclude auth tool from count
    )
    return manager, mcp_tools


def _mimir_mount_name_from_mcp_server_name(server_name: str) -> str | None:
    if not server_name.startswith("mimir-"):
        return None
    mount_name = server_name.removeprefix("mimir-").strip()
    return mount_name or None


def _mimir_ingest_event_fields_from_mcp_result(
    *,
    server_name: str,
    arguments: dict[str, Any],
    result: ToolResult,
) -> dict[str, Any] | None:
    if result.is_error:
        return None

    try:
        parsed = json.loads(result.content)
    except Exception:
        logger.debug("Failed to parse mimir_ingest MCP result from %s", server_name, exc_info=True)
        return None

    if not isinstance(parsed, dict):
        return None

    source_id = str(parsed.get("source_id") or "").strip()
    if not source_id:
        return None

    page_paths_raw = parsed.get("pages_updated")
    page_paths = page_paths_raw if isinstance(page_paths_raw, list) else []
    mount_name = _mimir_mount_name_from_mcp_server_name(server_name)
    source_title = str(parsed.get("title") or arguments.get("title") or source_id).strip()
    source_type = str(
        parsed.get("source_type") or arguments.get("source_type") or "document"
    ).strip()

    fields: dict[str, Any] = {
        "source_id": source_id,
        "source_title": source_title or source_id,
        "source_type": source_type or "document",
        "page_paths": [str(path) for path in page_paths if str(path).strip()],
        "mcp_server_name": server_name,
    }
    if mount_name:
        fields["mount_name"] = mount_name
        fields["mount_names"] = [mount_name]
    return fields


def _mimir_write_event_fields_from_mcp_result(
    *,
    server_name: str,
    arguments: dict[str, Any],
    result: ToolResult,
) -> dict[str, Any] | None:
    if result.is_error:
        return None

    page_path = str(arguments.get("path") or "").strip()
    if not page_path:
        content = str(result.content or "").strip()
        prefix = "Page written: "
        if content.startswith(prefix):
            page_path = content[len(prefix) :].split(" (routed to:", 1)[0].strip()
    if not page_path:
        return None

    explicit_mimir = str(arguments.get("mimir") or "").strip()
    mount_name = explicit_mimir or _mimir_mount_name_from_mcp_server_name(server_name)

    fields: dict[str, Any] = {
        "page_path": page_path,
        "mcp_server_name": server_name,
    }
    if mount_name:
        fields["mount_name"] = mount_name
        fields["mount_names"] = [mount_name]
    return fields


async def _shutdown_mcp(manager: Any | None) -> None:
    """Gracefully shut down MCP servers."""
    if manager is None:
        return
    try:
        await manager.shutdown()
    except Exception as exc:
        logger.warning("MCP shutdown error: %s", exc)


# ---------------------------------------------------------------------------
# User input function
# ---------------------------------------------------------------------------


async def _cli_user_input(question: str) -> str:
    """Prompt the user for input during ask_user tool calls."""
    return input(f"\n[Ravn asks] {question}\nYou: ").strip()


# ---------------------------------------------------------------------------
# Persona resolution
# ---------------------------------------------------------------------------


def _resolve_persona(
    persona_name: str,
    project_config: ProjectConfig | None,
    settings: Settings | None = None,
    cwd: Path | None = None,
) -> Any:
    """Load and merge a persona with optional ProjectConfig overrides."""
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

    persona = loader.load(name)
    if persona is None:
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


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


def _resolve_profile(profile_name: str) -> RavnProfile | None:
    """Load a RavnProfile by name from ~/.ravn/profiles/ or the built-in set.

    Returns ``None`` when *profile_name* is empty or cannot be resolved, so
    callers can proceed without a profile using Settings defaults.
    """
    from ravn.adapters.profiles.loader import ProfileLoader

    name = profile_name.strip()
    if not name:
        return None

    profile = ProfileLoader().load(name)
    if profile is None:
        typer.echo(f"Warning: profile '{name}' not found — using defaults.", err=True)
        return None

    return profile


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
        if persona_config.iteration_budget:
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
    from ravn.budget import IterationBudget
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
            if persona_config.iteration_budget:
                max_iterations = persona_config.iteration_budget
            if persona_config.llm.max_tokens:
                max_tokens = persona_config.llm.max_tokens

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
    iteration_budget = IterationBudget(
        total=settings.iteration_budget.total,
        near_limit_threshold=settings.iteration_budget.near_limit_threshold,
    )
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
        model=settings.effective_model(),
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
        reflection_config=settings.effective_post_session_reflection_config(),
        persona=persona_config.name if persona_config else "",
        persona_config=persona_config,
        stop_on_outcome=persona_config.stop_on_outcome if persona_config else False,
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
    ravn_profile = _resolve_profile(profile)
    # --persona overrides the profile's persona reference; if neither is given
    # the persona is taken from the profile (or ProjectConfig as fallback).
    effective_persona = persona or (ravn_profile.persona if ravn_profile else "")
    persona_config = _resolve_persona(effective_persona, project_config, settings=settings)

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
    ravn_profile = _resolve_profile(profile)
    effective_persona = persona or (ravn_profile.persona if ravn_profile else "")
    persona_config = _resolve_persona(effective_persona, project_config, settings=settings)

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
    from ravn.budget import IterationBudget
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
            if persona_config.iteration_budget:
                max_iterations = persona_config.iteration_budget

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
        budget = IterationBudget(
            total=settings.iteration_budget.total,
            near_limit_threshold=settings.iteration_budget.near_limit_threshold,
        )
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
            model=settings.effective_model(),
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
        False, "--resume", help="Resume unfinished tasks from the journal."
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
    ravn_profile = _resolve_profile(profile)
    effective_persona = persona or (ravn_profile.persona if ravn_profile else "")
    persona_config = _resolve_persona(effective_persona, project_config, settings=settings)

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
    ravn_profile = _resolve_profile(profile)
    effective_persona = persona or (ravn_profile.persona if ravn_profile else "")
    persona_config = _resolve_persona(effective_persona, project_config, settings=settings)

    asyncio.run(
        _run_daemon(
            settings,
            persona_config=persona_config,
            profile=ravn_profile,
            task_dispatch=True,
        )
    )


async def _run_daemon(
    settings: Settings,
    *,
    persona_config: Any | None = None,
    profile: RavnProfile | None = None,
    task_dispatch: bool = False,
    resume: bool = False,
) -> None:
    """Build and run the gateway + drive loop until interrupted."""
    from ravn.adapters.channels.gateway import RavnGateway
    from ravn.adapters.channels.gateway_http import HttpGateway
    from ravn.adapters.channels.gateway_telegram import TelegramGateway
    from ravn.budget import IterationBudget
    from ravn.drive_loop import DriveLoop
    from ravn.ports.channel import ChannelPort

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
            if persona_config.iteration_budget:
                max_iterations = persona_config.iteration_budget

    workspace = _resolve_workspace(settings)
    cli_transport_executor = _uses_cli_transport_executor(persona_config)
    llm = None if cli_transport_executor else _build_llm(settings)
    memory = _build_memory(settings)
    compressor = None if cli_transport_executor else _build_compressor(settings, llm)
    prompt_builder = _build_prompt_builder(settings)
    pre_hooks, post_hooks = _build_hooks(settings)

    extended_thinking = (
        settings.llm.extended_thinking
        if settings.llm.extended_thinking.enabled and not cli_transport_executor
        else None
    )

    mcp_manager: Any | None = None
    mcp_tools: list[Any] = []

    # Build Mímir adapter early so _agent_factory closure can capture it.
    daemon_mimir = _build_mimir(settings)
    drive_loop: Any | None = None

    # Populated by _wire_cron after drive_loop is created; captured by _agent_factory.
    cron_tools: list[Any] = []

    # NIU-598: shared in-process bus for post-session reflection (daemon mode).
    # Captured by _agent_factory so each agent created by the daemon publishes to it.
    daemon_bus: Any | None = None
    sleipnir_catalog_publisher: Any | None = None
    if settings.reflection.enabled:
        from sleipnir.adapters.in_process import InProcessBus

        daemon_bus = InProcessBus()

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
        if task_persona and task_persona != (persona_config.name if persona_config else None):
            task_persona_cfg = _resolve_persona(task_persona, None, settings=settings)
            if task_persona_cfg is not None:
                resolved_persona = task_persona_cfg
                if task_persona_cfg.system_prompt_template:
                    resolved_system_prompt = task_persona_cfg.system_prompt_template
                if task_persona_cfg.iteration_budget:
                    resolved_max_iterations = task_persona_cfg.iteration_budget
                if task_persona_cfg.llm.max_tokens:
                    resolved_max_tokens = task_persona_cfg.llm.max_tokens

        session = Session()
        budget = IterationBudget(
            total=settings.iteration_budget.total,
            near_limit_threshold=settings.iteration_budget.near_limit_threshold,
        )
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

            # Add cron scheduling tools (also filtered by persona)
            if cron_tools:
                tools.extend(_filter_tools(cron_tools, settings, resolved_persona))

        # NIU-571: Apply trust gradient constraints for thread-triggered tasks
        tools = _apply_trust_filter(tools, settings, triggered_by)

        # NIU-571: Apply trust gradient constraints for thread-triggered tasks
        tools = _apply_trust_filter(tools, settings, triggered_by)

        executor = _build_executor(resolved_persona)
        agent = executor.build(
            llm=llm,
            tools=tools,
            channel=channel,
            permission=permission,
            system_prompt=resolved_system_prompt,
            model=settings.effective_model(),
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
            prompt_builder=prompt_builder,
            reflection_model=settings.effective_memory_reflection_model(),
            reflection_max_tokens=settings.memory.reflection_max_tokens,
            task_summary_max_chars=settings.memory.task_summary_max_chars,
            input_token_cost_per_million=settings.memory.input_token_cost_per_million,
            output_token_cost_per_million=settings.memory.output_token_cost_per_million,
            extended_thinking=extended_thinking,
            # NIU-598: session lifecycle events + learnings injection
            sleipnir_publisher=daemon_bus,
            reflection_config=settings.effective_post_session_reflection_config(),
            persona=resolved_persona.name if resolved_persona else "",
            # NIU-612: persona config for outcome parsing + early termination
            persona_config=resolved_persona,
            stop_on_outcome=resolved_persona.stop_on_outcome if resolved_persona else False,
        )
        # Reviews and flock proposals must cross the mesh, not stay on the
        # daemon's in-process reflection bus — same precedence as the drive
        # loop's sleipnir publisher (closure binds late; the signal publisher
        # is built during daemon boot, before any task runs).
        return _attach_signal_build_tool(
            agent,
            workspace,
            triggered_by=triggered_by,
            settings=settings,
            publisher=environment_signal_publisher or sleipnir_catalog_publisher or daemon_bus,
            drive_loop=drive_loop,
        )

    tasks: list[asyncio.Task] = []

    # Create interaction tracker early so it can be shared between gateway
    # channels (touch on operator message) and the wakefulness trigger (read).
    from ravn.domain.interaction_tracker import LastInteractionTracker

    interaction_tracker = LastInteractionTracker()

    # Gateway channels (human-initiated turns)
    gw_tasks: list[str] = []
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
            ht = HttpGateway(channels_cfg.http, gw)
            tasks.append(asyncio.create_task(ht.run(), name="http"))
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
    resident_learning_runtime: Any | None = None
    resident_wakefulness: Any | None = None
    odin_court: Any | None = None
    feedback_recorder: Any | None = None
    huddle_archiver: Any | None = None
    environment_signal_publisher_started = False
    environment_signal_publisher: Any | None = _build_environment_signal_publisher(settings)
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

    if environment_signal_publisher is not None and hasattr(
        environment_signal_publisher,
        "start",
    ):
        await environment_signal_publisher.start()
        environment_signal_publisher_started = True

    resident_learning_runtime = _build_resident_learning_runtime(
        settings,
        publisher=environment_signal_publisher,
        workspace=_resolve_workspace(settings),
    )
    if resident_learning_runtime is not None:
        await resident_learning_runtime.start()
        tasks.append(asyncio.create_task(asyncio.Event().wait(), name="resident_learning"))
        typer.echo("  Resident learning: subscribed")

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


def _build_environment_signal_runtime(
    settings: Settings,
    *,
    drive_loop: Any | None = None,
    persona_config: Any | None = None,
    publisher: Any | None = None,
    mimir: Any | None = None,
    resident_learning_runtime: Any | None = None,
    resident_wakefulness: Any | None = None,
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
    resident_signal_recorder = None
    if (
        mimir is not None
        and settings.resident_inbox.enabled
        and settings.resident_inbox.environment_signals_enabled
    ):
        from ravn.resident_inbox import MimirResidentInbox  # noqa: PLC0415

        resident_signal_recorder = MimirResidentInbox(mimir)

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

    resident_id = settings.mesh.own_peer_id or f"valkyrie:{settings.environment.id}"
    local_ravn_dir = _resident_ravn_state_dir(workspace)
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
        learning_store=FlockLearningStore(local_ravn_dir / "flock_learning.json"),
        review_requester=review_requester,
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


def _split_workflow_edge_label(label: Any) -> tuple[str, str]:
    if not isinstance(label, str):
        return "", ""
    parts = [part.strip() for part in label.split("->", 1)]
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def _stage_personas(node: dict[str, Any]) -> set[str]:
    personas = {
        str(persona)
        for persona in (node.get("personaIds") or [])
        if isinstance(persona, str) and persona
    }
    for member in node.get("stageMembers") or []:
        if not isinstance(member, dict):
            continue
        persona_id = member.get("personaId")
        if isinstance(persona_id, str) and persona_id:
            personas.add(persona_id)
    return personas


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _join_mode_to_fan_in(join_mode: str) -> str:
    if join_mode == "all":
        return "all_must_pass"
    if join_mode == "any":
        return "any_pass"
    return "merge"


def _workflow_graph(settings: Settings) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    workflow_cfg = getattr(settings, "workflow", None)
    graph = getattr(workflow_cfg, "graph", None)
    if not isinstance(graph, dict):
        return [], []
    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
    return nodes, edges


def _normalize_workflow_event_filters(member: dict[str, Any]) -> dict[str, str]:
    raw_filters = member.get("eventFilters")
    if not isinstance(raw_filters, dict):
        raw_filters = member.get("event_filters")
    if not isinstance(raw_filters, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw_filters.items():
        key_text = str(key).strip()
        value_text = str(value).strip()
        if key_text and value_text:
            normalized[key_text] = value_text
    return normalized


def _workflow_event_matches_filters(
    payload: dict[str, Any],
    filters: dict[str, str],
) -> bool:
    if not filters:
        return True

    nested_fields = payload.get("fields")
    if not isinstance(nested_fields, dict):
        nested_fields = {}
    nested_outcome = payload.get("outcome")
    if not isinstance(nested_outcome, dict):
        nested_outcome = {}

    for key, expected in filters.items():
        actual: Any = None
        if key in payload:
            actual = payload.get(key)
        elif key in nested_fields:
            actual = nested_fields.get(key)
        elif key in nested_outcome:
            actual = nested_outcome.get(key)

        if isinstance(actual, list):
            if expected not in {str(item).strip() for item in actual if str(item).strip()}:
                return False
            continue

        if str(actual or "").strip() != expected:
            return False

    return True


def _workflow_runtime_for_persona(settings: Settings, persona_name: str) -> dict[str, Any] | None:
    nodes, edges = _workflow_graph(settings)
    if not nodes:
        return None

    matching_nodes: list[tuple[dict[str, Any], list[str], dict[str, str]]] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("kind") != "stage":
            continue
        stage_members = list(node.get("stageMembers") or [])
        for member in stage_members:
            if not isinstance(member, dict) or member.get("personaId") != persona_name:
                continue
            member_override_events: list[str] = []
            consumes_event_types = member.get("consumesEventTypes")
            if not isinstance(consumes_event_types, list):
                consumes_event_types = member.get("consumes_event_types")
            if isinstance(consumes_event_types, list):
                member_override_events.extend(
                    str(event_type).strip()
                    for event_type in consumes_event_types
                    if str(event_type).strip()
                )
            matching_nodes.append(
                (
                    node,
                    member_override_events,
                    _normalize_workflow_event_filters(member),
                )
            )
            break
        else:
            persona_ids = list(node.get("personaIds") or [])
            if persona_name in persona_ids:
                matching_nodes.append((node, [], {}))

    if not matching_nodes:
        return None

    consumer_groups: list[dict[str, Any]] = []
    aggregated_event_types: list[str] = []
    for index, (node, member_override_events, member_event_filters) in enumerate(matching_nodes):
        node_id = str(node.get("id") or f"{persona_name}-stage-{index}")
        group_event_types: list[str] = []
        for edge in edges:
            if str(edge.get("target")) != node_id:
                continue
            _, target_event = _split_workflow_edge_label(edge.get("label"))
            if target_event:
                group_event_types.append(target_event)

        resolved_event_types = _dedupe_preserve_order(member_override_events or group_event_types)
        if not resolved_event_types:
            continue

        fan_in_strategy = "merge"
        if len(resolved_event_types) > 1:
            fan_in_strategy = _join_mode_to_fan_in(str(node.get("joinMode") or "all"))

        consumer_groups.append(
            {
                "id": node_id,
                "label": str(node.get("label") or node_id),
                "event_types": resolved_event_types,
                "fan_in_strategy": fan_in_strategy,
                **({"event_filters": member_event_filters} if member_event_filters else {}),
            }
        )
        aggregated_event_types.extend(resolved_event_types)

    if not consumer_groups:
        return None

    default_strategy = (
        str(consumer_groups[0]["fan_in_strategy"]) if len(consumer_groups) == 1 else "merge"
    )

    return {
        "event_types": _dedupe_preserve_order(aggregated_event_types),
        "fan_in_strategy": default_strategy,
        "consumer_groups": consumer_groups,
    }


def _workflow_allowed_task_targets(
    settings: Settings,
    persona_name: str,
    *,
    node_id: str | None = None,
) -> set[str] | None:
    nodes, edges = _workflow_graph(settings)
    if not nodes or not edges:
        return None

    if node_id:
        matching_node_ids = {node_id}
    else:
        matching_node_ids = {
            str(node.get("id"))
            for node in nodes
            if node.get("kind") == "stage" and persona_name in _stage_personas(node)
        }
    if not matching_node_ids:
        return None

    allowed_personas: set[str] = set()
    for edge in edges:
        if str(edge.get("source")) not in matching_node_ids:
            continue
        target_id = str(edge.get("target"))
        if not target_id:
            continue
        for node in nodes:
            if str(node.get("id")) != target_id:
                continue
            if node.get("kind") != "stage":
                continue
            allowed_personas.update(_stage_personas(node))
            break

    return allowed_personas if allowed_personas else None


def _workflow_allowed_outcome_topics(
    settings: Settings,
    *,
    node_id: str | None,
) -> set[str] | None:
    if not node_id:
        return None
    nodes, edges = _workflow_graph(settings)
    if not nodes or not edges:
        return None
    topics = {
        source_event
        for edge in edges
        if str(edge.get("source")) == node_id
        for source_event, _target_event in [_split_workflow_edge_label(edge.get("label"))]
        if source_event
    }
    return topics if topics else None


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

            logger.info(
                "mesh: received outcome event_type=%s from=%s task_id=%s root=%s",
                event_type,
                source_persona,
                source_task_id,
                root_corr,
            )

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
                )

                if result is None:
                    pending_groups.append(group_id)
                    continue

                task_id_suffix = (root_corr or "unknown")[:8]
                safe_group_id = group_id.replace(".", "_").replace("-", "_")
                task = AgentTask(
                    task_id=f"event_{safe_group_id}_{task_id_suffix}",
                    title=f"Handle {result.triggered_by}",
                    initiative_context=result.merged_context,
                    triggered_by=result.triggered_by,
                    output_mode=OutputMode.SILENT,
                    persona=(
                        result.persona_name if result.persona_name != persona_config.name else None
                    ),
                    priority=5,
                    root_correlation_id=result.root_correlation_id,
                    workflow_parent_event_id=source_task_id,
                    workflow_node_id=group_id,
                )
                task.session_id = event.session_id or task.session_id

                try:
                    await drive_loop.enqueue(task)
                    logger.info(
                        "mesh: enqueued task %s for %s (fan-in: %s group=%s)",
                        task.task_id,
                        result.triggered_by,
                        group_strategy,
                        group_id,
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


def _build_mesh(settings: Settings, discovery: Any = None) -> Any:
    """Build mesh adapters using dynamic import from config.

    If settings.mesh.adapters is non-empty, uses the new list-based config.
    Otherwise falls back to legacy single-adapter mode for backward compatibility.

    All adapters run simultaneously via CompositeMeshAdapter:
    - publish() fans out to ALL transports
    - subscribe() registers on ALL transports
    - send() tries transports in order until success
    """
    import socket

    mesh_cfg = settings.mesh
    own_peer_id = mesh_cfg.own_peer_id or socket.gethostname()

    # New list-based config: delegate to shared niuu.mesh helper
    if mesh_cfg.adapters:
        from niuu.mesh import build_mesh_from_adapters_list  # noqa: PLC0415
        from niuu.mesh.transport_builder import build_transport  # noqa: PLC0415

        def _sleipnir_tb(entry: dict[str, Any]) -> Any:
            adapter = entry.get("transport", mesh_cfg.adapter or "nng")
            kwargs = _resolve_transport_kwargs(settings, adapter)
            if adapter in ("sleipnir", "rabbitmq") and not kwargs:
                return None
            return build_transport(adapter, **kwargs)

        return build_mesh_from_adapters_list(
            adapters=mesh_cfg.adapters,
            own_peer_id=own_peer_id,
            rpc_timeout_s=mesh_cfg.rpc_timeout_s,
            discovery=discovery,
            sleipnir_transport_builder=_sleipnir_tb,
        )

    # Legacy single-adapter mode for backward compatibility
    legacy_adapter = mesh_cfg.adapter or "nng"

    from niuu.mesh.transport_builder import build_transport  # noqa: PLC0415
    from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter  # noqa: PLC0415

    kwargs = _resolve_transport_kwargs(settings, legacy_adapter)
    if legacy_adapter in ("sleipnir", "rabbitmq") and not kwargs:
        logger.warning("mesh: failed to build transport, mesh disabled")
        return None

    transport = build_transport(legacy_adapter, **kwargs)
    if transport is None:
        logger.warning("mesh: failed to build transport, mesh disabled")
        return None

    return SleipnirMeshAdapter(
        publisher=transport,
        subscriber=transport,
        own_peer_id=own_peer_id,
        discovery=discovery,
        rpc_timeout_s=mesh_cfg.rpc_timeout_s,
    )


def _resolve_transport_kwargs(
    settings: Settings,
    adapter: str,
) -> dict[str, Any]:
    """Build constructor kwargs for a Sleipnir transport from settings."""
    if adapter == "nng":
        from niuu.mesh.cluster import read_cluster_pub_addresses  # noqa: PLC0415

        nng_cfg = settings.mesh.nng
        adapters_config = list(getattr(getattr(settings, "discovery", None), "adapters", []))
        peer_addresses = read_cluster_pub_addresses(adapters_config)
        return {
            "address": nng_cfg.pub_sub_address,
            "service_id": f"ravn:{settings.mesh.own_peer_id}",
            "peer_addresses": peer_addresses or None,
        }

    if adapter in ("sleipnir", "rabbitmq"):
        amqp_url = os.environ.get(settings.sleipnir.amqp_url_env, "")
        if not amqp_url:
            logger.warning(
                "mesh: %s not set, rabbitmq transport unavailable",
                settings.sleipnir.amqp_url_env,
            )
            return {}
        return {"amqp_url": amqp_url}

    if adapter == "nats":
        nats_cfg = settings.mesh.nats
        servers_raw = os.environ.get(nats_cfg.servers_env, "nats://localhost:4222")
        servers = [server.strip() for server in servers_raw.split(",") if server.strip()]
        kwargs: dict[str, Any] = {
            "servers": servers or ["nats://localhost:4222"],
            "stream_name": nats_cfg.stream_name,
            "jetstream_domain": nats_cfg.jetstream_domain,
            "subject_prefix": nats_cfg.subject_prefix,
            "retention": nats_cfg.retention,
            "max_age_seconds": nats_cfg.max_age_seconds,
            "max_bytes": nats_cfg.max_bytes,
            "ring_buffer_depth": nats_cfg.ring_buffer_depth,
            "connect_timeout_s": nats_cfg.connect_timeout_s,
            "max_reconnect_attempts": nats_cfg.max_reconnect_attempts,
            "ensure_stream": nats_cfg.ensure_stream,
            "publish_timeout_s": nats_cfg.publish_timeout_s,
            "tls_ca_file": nats_cfg.tls_ca_file,
            "tls_cert_file": nats_cfg.tls_cert_file,
            "tls_key_file": nats_cfg.tls_key_file,
            "tls_hostname": nats_cfg.tls_hostname,
            "tls_handshake_first": nats_cfg.tls_handshake_first,
            "tls_insecure_skip_verify": nats_cfg.tls_insecure_skip_verify,
            "user": os.environ.get(nats_cfg.user_env, nats_cfg.user)
            if nats_cfg.user_env
            else nats_cfg.user,
            "password": os.environ.get(nats_cfg.password_env, "") if nats_cfg.password_env else "",
            "token": os.environ.get(nats_cfg.token_env, "") if nats_cfg.token_env else "",
            "nkeys_seed_file": nats_cfg.nkeys_seed_file,
            "nkeys_seed": os.environ.get(nats_cfg.nkeys_seed_env, "")
            if nats_cfg.nkeys_seed_env
            else "",
            "extra_subscriptions": [
                {
                    "subject": entry.subject,
                    "stream_name": entry.stream_name,
                    "event_types": list(entry.event_types),
                }
                for entry in nats_cfg.extra_subscriptions
                if entry.subject
            ],
            "core_subscriptions": [
                {"subject": entry.subject} for entry in nats_cfg.core_subscriptions if entry.subject
            ],
        }
        if nats_cfg.consumer_group:
            kwargs["consumer_group"] = nats_cfg.consumer_group
        if nats_cfg.replay_from_sequence is not None:
            kwargs["replay_from_sequence"] = nats_cfg.replay_from_sequence
        return kwargs

    if adapter == "redis":
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        return {"redis_url": redis_url}

    return {}


def _build_discovery(
    settings: Settings,
    persona_config: Any | None = None,
    profile_name: str = "default",
) -> Any:
    """Build the discovery adapter from config, wiring the own identity."""
    import importlib.metadata

    from ravn.adapters.discovery._identity import (
        load_or_create_peer_id,
        load_or_create_realm_key,
        realm_id_from_key,
    )
    from ravn.domain.models import RavnIdentity

    peer_id = settings.mesh.own_peer_id or load_or_create_peer_id()
    realm_key = load_or_create_realm_key()
    realm_id = realm_id_from_key(realm_key)

    try:
        version = importlib.metadata.version("ravn")
    except Exception:
        version = "0.0.0"

    # Advertise addresses that remote peers can connect to.
    # Replace nng wildcard listen address (*) with 127.0.0.1 for same-host meshes.
    rep_address = settings.mesh.nng.req_rep_address.replace("*", "127.0.0.1")
    pub_address = settings.mesh.nng.pub_sub_address.replace("*", "127.0.0.1")

    persona_name = (
        getattr(persona_config, "name", None) or settings.agent.system_prompt[:30] or "ravn"
    )
    capabilities = _derive_capabilities(settings, persona_config, profile_name)

    # Extract event types this persona consumes (for mesh routing)
    consumes_event_types: list[str] = []
    if persona_config is not None and hasattr(persona_config, "consumes"):
        consumes_event_types = list(persona_config.consumes.event_types or [])

    identity = RavnIdentity(
        peer_id=peer_id,
        realm_id=realm_id,
        persona=persona_name,
        capabilities=capabilities,
        permission_mode=settings.permission.mode,
        version=version,
        consumes_event_types=consumes_event_types,
        rep_address=rep_address,
        pub_address=pub_address,
    )

    from niuu.mesh.discovery_builder import build_discovery_adapters  # noqa: PLC0415

    return build_discovery_adapters(
        adapters_config=list(getattr(settings.discovery, "adapters", [])),
        own_identity=identity,
        heartbeat_interval_s=settings.discovery.heartbeat_interval_s,
        peer_ttl_s=settings.discovery.peer_ttl_s,
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


async def _run_peers(settings: Settings, *, verbose: bool, force_scan: bool) -> None:
    """Build a discovery adapter, optionally scan, and print the peer table."""
    from ravn.adapters.discovery._identity import (
        load_or_create_peer_id,
        load_or_create_realm_key,
        realm_id_from_key,
    )
    from ravn.domain.models import RavnIdentity

    peer_id = load_or_create_peer_id()
    realm_key = load_or_create_realm_key()
    realm_id = realm_id_from_key(realm_key)

    import importlib.metadata

    try:
        version = importlib.metadata.version("ravn")
    except Exception:
        version = "0.0.0"

    identity = RavnIdentity(
        peer_id=peer_id,
        realm_id=realm_id,
        persona=settings.agent.system_prompt[:30] if settings.agent.system_prompt else "ravn",
        capabilities=[],
        permission_mode=settings.permission.mode,
        version=version,
    )

    from niuu.mesh.discovery_builder import build_discovery_adapters  # noqa: PLC0415

    discovery = build_discovery_adapters(
        adapters_config=list(getattr(settings.discovery, "adapters", [])),
        own_identity=identity,
        heartbeat_interval_s=settings.discovery.heartbeat_interval_s,
        peer_ttl_s=settings.discovery.peer_ttl_s,
    )
    if discovery is None:
        typer.echo("No discovery adapter configured.", err=True)
        return

    await discovery.start()

    # Wait for mDNS announcements from peers to arrive before querying the table.
    convergence_wait = getattr(settings.discovery.mdns, "convergence_wait_s", 3.0)
    await asyncio.sleep(convergence_wait)

    if force_scan:
        candidates = await discovery.scan()
        typer.echo(f"Scan found {len(candidates)} candidate(s).")
        for c in candidates:
            if c.peer_id not in discovery.peers():
                peer = await discovery.handshake(c)
                if peer is not None:
                    typer.echo(f"  Handshook with {c.peer_id}")

    verified = discovery.peers()
    if not verified:
        typer.echo("No verified flock members found.")
        await discovery.stop()
        return

    typer.echo(f"Flock members ({len(verified)}):")
    for pid, peer in sorted(verified.items()):
        caps = ", ".join(peer.capabilities) if peer.capabilities else "—"
        line = f"  {pid:<20}  {peer.persona:<20} [{peer.status}]  caps={caps}"
        if verbose:
            rep = peer.rep_address or "—"
            pub = peer.pub_address or "—"
            latency = f"{peer.latency_ms:.1f}ms" if peer.latency_ms is not None else "—"
            line += f"\n    rep={rep}  pub={pub}  latency={latency}  tasks={peer.task_count}"
            line += f"  last_seen={peer.last_seen.isoformat()}"
        typer.echo(line)

    await discovery.stop()


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
def room(
    action: str = typer.Argument(
        help="Room action: join | leave | message | heartbeat | close | participants."
    ),
    broker_url: str = typer.Option(
        "http://127.0.0.1:9000",
        "--broker-url",
        envvar="SKULD_BROKER_URL",
        help="Skuld broker base URL hosting the Environment room.",
    ),
    participant: str = typer.Option("", "--participant", help="Participant id, e.g. human:jozef."),
    environment: str = typer.Option("", "--environment", help="Environment id to join."),
    role: str = typer.Option(
        "observer", "--role", help="Room role: observer|teacher|approver|debugger|owner."
    ),
    room_id: str = typer.Option("", "--room", help="Optional huddle room id."),
    text: str = typer.Option("", "--text", help="Message text (message action)."),
    reason: str = typer.Option("left", "--reason", help="Reason for leave/close."),
) -> None:
    """Join, talk in, and leave a live Valkyrie Environment room from the CLI.

    Wraps the Skuld broker room API so a terminal operator participates in
    the same live Environment state as the UI (NIU-1023).
    """
    import httpx  # noqa: PLC0415

    base = broker_url.rstrip("/")

    def _post(path: str, payload: dict) -> dict:
        response = httpx.post(f"{base}{path}", json=payload, timeout=10.0)
        if response.status_code >= 400:
            typer.echo(f"error {response.status_code}: {response.text[:300]}", err=True)
            raise typer.Exit(1)
        return response.json()

    if action == "join":
        if not participant or not environment:
            typer.echo("join requires --participant and --environment", err=True)
            raise typer.Exit(2)
        result = _post(
            "/api/room/join",
            {
                "participant_id": participant,
                "display_name": participant,
                "environment_id": environment,
                "role": role,
                "room_id": room_id,
            },
        )
        meta = result.get("participant", result)
        typer.echo(
            f"joined {environment} as {participant} ({role}); "
            f"capabilities: {', '.join(meta.get('capabilities', []))}"
        )
        return
    if action == "leave":
        _post("/api/room/leave", {"participant_id": participant, "reason": reason})
        typer.echo(f"left: {participant}")
        return
    if action == "message":
        if not text:
            typer.echo("message requires --text", err=True)
            raise typer.Exit(2)
        _post(
            "/api/room/message",
            {"participant_id": participant, "content": text, "room_id": room_id},
        )
        typer.echo("sent")
        return
    if action == "heartbeat":
        _post("/api/room/heartbeat", {"participant_id": participant})
        typer.echo("heartbeat recorded")
        return
    if action == "close":
        result = _post(
            "/api/room/close",
            {"room_id": room_id, "reason": reason},
        )
        typer.echo(f"closed {room_id}; transcript: {result.get('transcriptRef', '-')}")
        return
    if action == "participants":
        response = httpx.get(f"{base}/api/room/participants", timeout=10.0)
        response.raise_for_status()
        for entry in response.json().get("participants", []):
            typer.echo(
                f"- {entry.get('peer_id')} [{entry.get('participant_type')}] "
                f"{entry.get('authority_role') or ''} {entry.get('status') or ''}"
            )
        return
    typer.echo(f"unknown action: {action}", err=True)
    raise typer.Exit(2)


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
# Momentum Packet CLI
# ---------------------------------------------------------------------------

momentum_app = typer.Typer(
    name="momentum",
    help="Momentum Packet proof utilities.",
    add_completion=False,
)
app.add_typer(momentum_app, name="momentum")

_MOMENTUM_DISPOSITION_OUTCOMES = ("accepted", "dismissed", "wrong", "deferred", "acted")


@momentum_app.command("extract")
def momentum_extract_cmd(
    path: str = typer.Argument(..., help="Markdown resident signal file."),
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
) -> None:
    """Extract resident understanding, judgment, and maybe one packet from a signal."""
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)
    asyncio.run(_run_momentum_extract(settings, path))


@momentum_app.command("eval")
def momentum_eval_cmd(
    path: str = typer.Argument(..., help="Noisy markdown transcript fixture."),
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
) -> None:
    """Run the opt-in real-LLM Momentum Packet eval."""
    if os.environ.get("RAVN_LLM_EVAL") != "1":
        typer.echo("Skipped: set RAVN_LLM_EVAL=1 to run the real LLM eval.")
        return
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)
    asyncio.run(_run_momentum_eval(settings, path))


@momentum_app.command("inbox")
def momentum_inbox_cmd(
    signal_ref_or_id: str = typer.Argument(..., help="Resident inbox signal ref or id."),
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
) -> None:
    """Run Momentum extraction from a resident inbox signal."""
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)
    asyncio.run(_run_momentum_inbox(settings, signal_ref_or_id))


@momentum_app.command("reflect")
def momentum_reflect_cmd(
    target_ref: str = typer.Argument(..., help="Momentum run or judgment artifact ref."),
    outcome: str = typer.Option(
        ...,
        "--outcome",
        help="Disposition: accepted, dismissed, wrong, deferred, or acted.",
    ),
    note: str = typer.Option(..., "--note", help="What happened to this judgment."),
    actor: str = typer.Option("operator", "--actor", help="Disposition actor."),
    config: str = typer.Option("", "--config", "-c", help="Path to ravn config YAML."),
) -> None:
    """Record a judgment disposition and model-authored reflection."""
    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = Settings()
    _configure_logging(settings)
    asyncio.run(_run_momentum_reflect(settings, target_ref, outcome, note, actor))


async def _run_momentum_extract(settings: Settings, path: str) -> None:
    from ravn.adapters.resident_signal import MarkdownResidentSignalSource

    result = await _run_momentum_source(settings, MarkdownResidentSignalSource(), path)
    _print_momentum_result(result)


async def _run_momentum_inbox(settings: Settings, signal_ref_or_id: str) -> None:
    source = _build_resident_inbox_signal_source(settings)
    result = await _run_momentum_source(settings, source, signal_ref_or_id)
    _print_momentum_result(result)


async def _run_momentum_reflect(
    settings: Settings,
    target_ref: str,
    outcome: str,
    note: str,
    actor: str,
) -> None:
    from ravn.momentum import (
        MomentumExtractionWorker,
        MomentumPipeline,
        MomentumReflectionWorker,
    )

    if outcome not in _MOMENTUM_DISPOSITION_OUTCOMES:
        allowed = ", ".join(_MOMENTUM_DISPOSITION_OUTCOMES)
        typer.echo(f"Invalid outcome: {outcome}. Allowed values: {allowed}", err=True)
        raise typer.Exit(2)

    workspace = _resolve_workspace(settings)
    state = await _build_resident_state(settings, workspace)
    llm = _build_llm(settings)
    model = settings.effective_model()
    result = await MomentumPipeline(
        worker=MomentumExtractionWorker(llm, model=model),
        reflection_worker=MomentumReflectionWorker(llm, model=model),
        state=state,
    ).reflect_judgment(target_ref, outcome=outcome, note=note, actor=actor)
    typer.echo(f"disposition_ref: {result.disposition_ref}")
    typer.echo(f"reflection_ref:  {result.reflection_ref}")


def _print_momentum_result(result: Any) -> None:
    typer.echo(f"run_id:      {result.extraction.run.run_id}")
    typer.echo(f"run_ref:     {result.run_ref}")
    typer.echo(f"artifacts:   {len(result.artifact_refs)}")
    typer.echo(f"judgment_ref:{result.judgment_ref}")
    typer.echo(f"packet_ref:  {result.packet_ref or '-'}")
    if result.extraction.packet is not None:
        typer.echo(f"packet:      {result.extraction.packet.title}")
    typer.echo(f"provenance:  {_provenance_label(result.provenance_fully_verified)}")


async def _run_momentum_eval(settings: Settings, path: str) -> None:
    from ravn.adapters.resident_signal import MarkdownResidentSignalSource
    from ravn.momentum.eval import evaluate_extraction

    result = await _run_momentum_source(settings, MarkdownResidentSignalSource(), path)
    errors = evaluate_extraction(result.extraction)
    typer.echo(f"run_id:      {result.extraction.run.run_id}")
    typer.echo(f"run_ref:     {result.run_ref}")
    typer.echo(f"judgment_ref:{result.judgment_ref}")
    typer.echo(f"packet_ref:  {result.packet_ref or '-'}")
    typer.echo(f"artifacts:   {len(result.artifact_refs)}")
    typer.echo(f"provenance:  {_provenance_label(result.provenance_fully_verified)}")
    if errors:
        for error in errors:
            typer.echo(f"eval_error:  {error}", err=True)
        raise typer.Exit(1)
    typer.echo("eval:        ok")


async def _run_momentum_source(settings: Settings, source: Any, ref_or_id: str) -> Any:
    from ravn.momentum import MomentumExtractionWorker, MomentumPipeline

    try:
        signal = await source.load_signal(ref_or_id)
    except FileNotFoundError:
        typer.echo(f"Resident signal not found: {ref_or_id}", err=True)
        raise typer.Exit(1) from None
    except IsADirectoryError:
        typer.echo(f"Not a file: {ref_or_id}", err=True)
        raise typer.Exit(1) from None

    workspace = _resolve_workspace(settings)
    state = await _build_resident_state(settings, workspace)
    llm = _build_llm(settings)
    worker = MomentumExtractionWorker(llm, model=settings.effective_model())
    return await MomentumPipeline(worker=worker, state=state).extract_signal(signal)


def _build_resident_inbox_signal_source(settings: Settings) -> Any:
    from ravn.adapters.resident_signal import MimirResidentInboxSignalSource

    mimir = _build_mimir(settings)
    if mimir is None:
        typer.echo("Mímir is required to read resident inbox signals.", err=True)
        raise typer.Exit(1)
    return MimirResidentInboxSignalSource(mimir)


def _provenance_label(verified: bool) -> str:
    return "verified" if verified else "unverified"


async def _build_resident_state(settings: Settings, workspace: Path) -> Any:
    from ravn.adapters.resident_state import select_resident_state

    cfg = settings.resident_state
    root = _resident_ravn_state_dir(workspace) / "resident-state"
    preferred = _build_resident_state_adapter(
        cfg.adapter,
        cfg.kwargs,
        cfg.secret_kwargs_env,
        default_root=root / "preferred",
    )
    fallback = _build_resident_state_adapter(
        cfg.fallback_adapter,
        cfg.fallback_kwargs,
        cfg.fallback_secret_kwargs_env,
        default_root=root / "fallback",
    )
    return await select_resident_state(preferred, fallback)


def _build_resident_state_adapter(
    adapter: str,
    kwargs: dict[str, Any],
    secret_kwargs_env: dict[str, str],
    *,
    default_root: Path,
) -> Any:
    cls = _import_class(adapter)
    merged = _inject_secrets(dict(kwargs), secret_kwargs_env)
    if _constructor_accepts_kwarg(cls, "root"):
        merged.setdefault("root", default_root)
    return cls(**merged)


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
            from ravn.domain.mimir import MimirAuth

            auth = None
            if inst.auth is not None:
                auth = MimirAuth(
                    type=inst.auth.type,
                    token=_resolve_mimir_auth_token(inst.auth)
                    if inst.auth.type == "bearer"
                    else None,
                    token_file=inst.auth.token_file,
                    exchange_url=inst.auth.exchange_url,
                    audiences=tuple(inst.auth.audiences),
                    trust_domain=inst.auth.trust_domain,
                )
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
