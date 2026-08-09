"""Runtime adapter and infrastructure builders for the Ravn CLI."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
from collections.abc import Awaitable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from niuu.adapters.outbound.http_auth import WorkloadIdentityBearerTokenAuthAdapter
from ravn.config import Settings, ToolGroupConfig
from ravn.ports.executor import ExecutorPort

logger = logging.getLogger(__name__)


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


def _resident_ravn_state_dir(workspace: Path, settings: Settings | None = None) -> Path:
    """Return the writable resident Ravn state directory for skills/metadata."""
    override = (settings or Settings()).state_dir.strip()
    if override:
        return Path(override).expanduser()
    if os.access(workspace, os.W_OK):
        return workspace / ".ravn"
    return Path.home() / ".ravn"


def _attach_agent_build_tool(
    agent: Any,
    workspace: Path,
    *,
    enabled: bool,
    settings: Settings,
    publisher: Any | None = None,
    drive_loop: Any | None = None,
    a2a_activity_emitter: Any | None = None,
    installed_artifact_recorder: Any | None = None,
) -> Any:
    """Attach build_tool when the active persona explicitly allows it."""
    if not enabled:
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

        state_dir = _resident_ravn_state_dir(workspace, settings)
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
        realm_config = _resolve_realm_build_config(settings)

        # The resident's own model handles INPUT_REQUIRED on commissioned
        # builds within the realm autonomy grant: it ANSWERS genuine peer
        # questions (help_needed) with information, and DECIDES workflow
        # gates approve/request_changes.
        gate_reviewer = None
        question_answerer = None
        agent_llm = getattr(agent, "llm", None)
        if agent_llm is not None:
            from ravn.adapters.tool_build.gate_review import (  # noqa: PLC0415
                build_llm_gate_reviewer,
                build_llm_question_answerer,
            )

            gate_reviewer = build_llm_gate_reviewer(
                llm=agent_llm,
                model=settings.effective_model(),
                valkyrie_id=valkyrie_id,
            )
            question_answerer = build_llm_question_answerer(
                llm=agent_llm,
                model=settings.effective_model(),
                valkyrie_id=valkyrie_id,
            )

        attach_build_tool(
            agent,
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            publisher=publisher,
            review_requester=review_requester,
            autonomy_mode=realm_config.autonomy_mode,
            environment_id=settings.environment.id,
            valkyrie_id=valkyrie_id,
            flock_id=settings.environment.flocks[0] if settings.environment.flocks else "",
            domain=settings.environment.type,
            execution_backend=settings.resident_evolution.learned_tool_execution_backend,
            execution_backend_kwargs=(
                settings.resident_evolution.learned_tool_k8s.model_dump()
                if settings.resident_evolution.learned_tool_execution_backend == "k8s_job"
                else None
            ),
            workspace_root=workspace,
            build_backend=_build_tool_build_backend(
                settings,
                workflow_selector=realm_config.workflow_selector,
                gate_reviewer=gate_reviewer,
                question_answerer=question_answerer,
                activity_emitter=a2a_activity_emitter,
            ),
            investigation_context=_investigation_context,
            max_repair_attempts=settings.resident_evolution.build_repair_attempts,
            flock_confidence=settings.resident_evolution.self_registered_tool_confidence,
            installed_artifact_recorder=installed_artifact_recorder,
        )
    except TypeError:
        logger.debug("build_tool not attached: executor does not support dynamic tools")
    except Exception as exc:
        logger.warning("Failed to attach build_tool to signal investigation: %s", exc)
    return agent


def _build_tool_build_backend(
    settings: Settings,
    *,
    workflow_selector: dict[str, Any] | None = None,
    gate_reviewer: Any | None = None,
    question_answerer: Any | None = None,
    activity_emitter: Any | None = None,
) -> Any | None:
    """Construct the configured tool build adapter, or None for inline authoring.

    ``workflow_selector``, when provided (resolved from a realm build grant),
    overrides the static ``tool_builder_workflow`` config for this backend.
    ``gate_reviewer`` / ``question_answerer`` (the resident's LLM-backed
    judgment and knowledge) are passed to backends that accept them so
    INPUT_REQUIRED tasks can be handled over A2A.
    """
    cfg = settings.resident_evolution
    if not cfg.tool_build_adapter:
        return None
    cls = _import_class(cfg.tool_build_adapter)
    kwargs = dict(cfg.tool_build_kwargs)
    selector_dict = workflow_selector
    if selector_dict is None:
        static_selector = cfg.tool_builder_workflow
        if static_selector.names or static_selector.tags:
            selector_dict = static_selector.model_dump()
    if selector_dict and _constructor_accepts_kwarg(cls, "workflow_selector"):
        kwargs["workflow_selector"] = selector_dict
    if gate_reviewer is not None and _constructor_accepts_kwarg(cls, "gate_reviewer"):
        kwargs["gate_reviewer"] = gate_reviewer
    if question_answerer is not None and _constructor_accepts_kwarg(cls, "question_answerer"):
        kwargs["question_answerer"] = question_answerer
    if activity_emitter is not None and _constructor_accepts_kwarg(cls, "activity_emitter"):
        kwargs["activity_emitter"] = activity_emitter
    if _constructor_accepts_kwarg(cls, "push_callback_url"):
        kwargs["push_callback_url"] = settings.gateway.platform.a2a_push_callback_url
    return cls(**kwargs)


class _RealmBuildConfig:
    """Effective tool-build config after realm-grant resolution.

    ``workflow_selector`` is ``None`` when no realm grant pins a workflow (the
    static ``tool_builder_workflow`` then applies); ``autonomy_mode`` always
    carries the effective mode (realm-derived or the static default).
    """

    def __init__(
        self,
        *,
        autonomy_mode: str,
        workflow_selector: dict[str, Any] | None,
    ) -> None:
        self.autonomy_mode = autonomy_mode
        self.workflow_selector = workflow_selector


def _run_coro_blocking(coro: Awaitable[Any]) -> Any:
    """Run a coroutine to completion from sync wiring code, loop-safe.

    Uses ``asyncio.run`` when no loop is running (plain CLI). Inside a running
    loop — the daemon's agent factory is called from the async drive loop — a
    dedicated thread runs the coroutine on its own loop, because
    ``asyncio.run`` would raise RuntimeError there and realm resolution would
    silently degrade to static config.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()  # type: ignore[arg-type]


#: RealmClients cached per (base_url + auth kwargs): the daemon's agent factory
#: resolves realm config per task, and a fresh client per call would mean a
#: fresh auth adapter — and a token exchange — per task. The auth adapter
#: caches its token with expiry skew, so a reused client only re-exchanges on
#: expiry. Bounded by the number of distinct configs in one process (~1).
_REALM_CLIENT_CACHE: dict[tuple, Any] = {}


def _realm_client_for(settings: Settings) -> Any:
    """The canonical RealmClient factory (cached), or None when unusable."""
    from ravn.adapters.realm import RealmClient, build_realm_client_kwargs  # noqa: PLC0415

    cfg = settings.resident_evolution
    base_url = cfg.realm_api_base_url or str(cfg.tool_build_kwargs.get("base_url") or "")
    if not base_url:
        logger.warning(
            "realm_slug %r is set but no realm_api_base_url or "
            "tool_build_kwargs['base_url'] is configured; using static tool-build config",
            cfg.realm_slug,
        )
        return None
    auth_kwargs = build_realm_client_kwargs(
        realm_api_kwargs=cfg.realm_api_kwargs,
        tool_build_kwargs=cfg.tool_build_kwargs,
    )
    cache_key = (base_url, tuple(sorted((k, str(v)) for k, v in auth_kwargs.items())))
    client = _REALM_CLIENT_CACHE.get(cache_key)
    if client is None:
        client = RealmClient(base_url=base_url, **auth_kwargs)
        _REALM_CLIENT_CACHE[cache_key] = client
    return client


def _resolve_realm_build_config(settings: Settings) -> _RealmBuildConfig:
    """Resolve tool-build workflow + autonomy from the realm's build trust grant.

    Falls back to static config when ``realm_slug`` is empty, when the realm is
    unreachable (logs WARNING — a realm outage must not brick the resident), or
    when the realm has no build grant. NEVER pretends a grant exists. Reaches
    the realm API at the same Volundr base_url Ravn uses for Forge sessions.
    """
    cfg = settings.resident_evolution
    static = _RealmBuildConfig(autonomy_mode=cfg.autonomy_mode, workflow_selector=None)
    if not cfg.realm_slug:
        logger.info("no realm_slug configured; using static tool-build config")
        return static

    client = _realm_client_for(settings)
    if client is None:
        return static

    try:
        grant = _run_coro_blocking(client.resolve_build_grant(cfg.realm_slug))
    except Exception as exc:  # noqa: BLE001 — realm outage must not brick the resident
        logger.warning(
            "realm %s build-grant resolution failed (%s); using static tool-build config",
            cfg.realm_slug,
            exc,
        )
        return static

    if grant is None:
        logger.info(
            "realm %s has no build grant; using static tool-build config",
            cfg.realm_slug,
        )
        return static

    from ravn.adapters.realm import (  # noqa: PLC0415
        autonomy_mode_for_trust_level,
        workflow_selector_from_grant,
    )

    trust_table = cfg.trust_level_autonomy_table
    autonomy_mode = autonomy_mode_for_trust_level(
        grant.level,
        autonomous_threshold=trust_table.autonomous,
        yolo_threshold=trust_table.yolo,
    )
    workflow_selector = workflow_selector_from_grant(grant)
    logger.info(
        "realm %s build grant resolved: level=%d autonomy_mode=%s workflow_selector=%s",
        cfg.realm_slug,
        grant.level,
        autonomy_mode,
        workflow_selector,
    )
    return _RealmBuildConfig(
        autonomy_mode=autonomy_mode,
        workflow_selector=workflow_selector,
    )


def _constructor_accepts_kwarg(cls: type, name: str) -> bool:
    signature = inspect.signature(cls)
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == name:
            return True
    return False


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

    # No provider substitution. Rotating to a second provider mid-run means the
    # answer came from a model nobody chose for it, decided at runtime and
    # usually unnoticed. If the configured provider cannot serve, the call
    # fails; changing provider is a deploy.
    return primary


def _build_executor(
    persona_config: Any | None = None,
    settings: Settings | None = None,
) -> ExecutorPort:
    """Build the runtime-selected executor adapter.

    Persona-level runtime overrides win so mixed-provider workflows can run
    different transports in one flock. Session/workload transport settings are
    used as the default when a persona does not override them.
    """
    loaded_settings = settings or Settings()
    adapter_path = "ravn.adapters.executors.agent.AgentExecutor"
    kwargs: dict[str, Any] = {}

    executor_cfg = getattr(persona_config, "executor", None)
    if executor_cfg is not None and getattr(executor_cfg, "adapter", ""):
        adapter_path = str(executor_cfg.adapter)
        raw_kwargs = getattr(executor_cfg, "kwargs", {})
        if isinstance(raw_kwargs, dict):
            kwargs = dict(raw_kwargs)
        if adapter_path == "ravn.adapters.executors.cli.CliTransportExecutor":
            transport_adapter = str(kwargs.get("transport_adapter") or "").strip()
            runtime_kwargs = _runtime_cli_transport_kwargs(
                transport_adapter,
                loaded_settings,
            )
            configured_kwargs = kwargs.get("transport_kwargs")
            if runtime_kwargs and (
                configured_kwargs is None or isinstance(configured_kwargs, dict)
            ):
                kwargs["transport_kwargs"] = {
                    **runtime_kwargs,
                    **(configured_kwargs or {}),
                }
    else:
        runtime_transport_adapter = loaded_settings.runtime_executor.transport_adapter.strip()
        if runtime_transport_adapter:
            adapter_path = "ravn.adapters.executors.cli.CliTransportExecutor"
            kwargs = {"transport_adapter": runtime_transport_adapter}
            transport_kwargs = _runtime_cli_transport_kwargs(
                runtime_transport_adapter, loaded_settings
            )
            if transport_kwargs:
                kwargs["transport_kwargs"] = transport_kwargs

    cls = _import_class(adapter_path)
    return cls(**kwargs)


def _runtime_cli_transport_kwargs(
    transport_adapter: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Return explicit transport kwargs advertised by the runtime environment."""
    if transport_adapter != "skuld.transports.codex_ws.CodexWebSocketTransport":
        return {}

    resolved = settings or Settings()
    config = resolved.runtime_executor
    kwargs: dict[str, Any] = {}
    if config.skip_permissions is not None:
        kwargs["skip_permissions"] = config.skip_permissions
    if config.approval_policy:
        kwargs["approval_policy"] = config.approval_policy
    if config.sandbox:
        kwargs["sandbox"] = config.sandbox

    provider = _codex_auth_provider(resolved)
    if provider is not None:
        kwargs["codex_auth_provider"] = provider

    return kwargs


_WORKLOAD_EXCHANGE_URL_ENV = "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL"


def _platform_origin_from_exchange_url() -> str:
    """Return the Völundr origin implied by the injected exchange URL.

    Bootstrap wiring supplied by the runtime, not an application knob — the
    same env the workload identity adapters already read.
    """
    raw = os.environ.get(_WORKLOAD_EXCHANGE_URL_ENV, "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _codex_auth_provider(settings: Settings) -> Any | None:
    """Build the configured Codex auth provider, mirroring Skuld's broker.

    Skuld injects one of these whenever the transport class accepts it. A flock
    persona runs the same transport but constructs it through Ravn's CLI
    executor, so without this it opens the Codex websocket with no credential
    and every turn dies on 401. Same adapter, same broker endpoint, one
    contract.

    Returns None when no adapter is configured — a host Codex login needs no
    provider, and that is the historical default.
    """
    adapter = settings.runtime_executor.codex_auth_adapter.strip()
    if not adapter:
        return None

    platform = settings.gateway.platform
    base_url = platform.base_url.strip()
    # Explicitly configured, not merely defaulted. base_url falls back to
    # http://localhost:8080, which is right for a developer running Völundr
    # locally and meaningless inside a flock pod — building a provider against
    # it would turn a missing-config error into a confusing connection failure
    # at the first model call.
    if "base_url" not in platform.model_fields_set or not base_url:
        # A flock persona carries no platform block, but the runtime does inject
        # the workload exchange URL, which is by construction on the Völundr
        # that brokers this credential. Same host, same trust, already present.
        base_url = _platform_origin_from_exchange_url()
    if not base_url:
        logger.warning(
            "runtime_executor: codex_auth_adapter %s is configured but no "
            "Völundr base URL is available from gateway.platform.base_url or "
            "%s, so there is no broker to call; leaving the transport "
            "unauthenticated",
            adapter,
            _WORKLOAD_EXCHANGE_URL_ENV,
        )
        return None

    # Pass token_file/exchange_url only when they were actually configured.
    # Both fall back to NIUU_WORKLOAD_IDENTITY_* inside the adapter, and in a
    # flock persona those env vars are the correct answer while Ravn's own
    # defaults are not: workload_token_file defaults to the generic
    # kubernetes.io service-account token, whose audience the exchange rejects.
    # Handing over the default silently replaced a working credential with one
    # that could only ever 401.
    auth_kwargs: dict[str, Any] = {"base_url": base_url}
    if "workload_token_file" in platform.model_fields_set:
        auth_kwargs["token_file"] = platform.workload_token_file
    if "workload_exchange_url" in platform.model_fields_set:
        auth_kwargs["exchange_url"] = platform.workload_exchange_url
    if "workload_audiences" in platform.model_fields_set:
        auth_kwargs["audiences"] = list(platform.workload_audiences)
    auth = WorkloadIdentityBearerTokenAuthAdapter(**auth_kwargs)

    async def _client() -> httpx.AsyncClient:
        # HttpAuthPort.headers() is synchronous — it performs the token
        # exchange inline and returns a dict. The provider calls this factory
        # with await, which is about the factory, not about headers().
        return httpx.AsyncClient(base_url=base_url, headers=auth.headers())

    cls = _import_class(adapter)
    return cls(
        http_client_provider=_client,
        **dict(settings.runtime_executor.codex_auth_kwargs),
    )


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


def _uses_cli_transport_runtime(settings: Settings | None = None) -> bool:
    """Return True when the daemon is configured to execute turns via CLI transport."""
    return bool((settings or Settings()).runtime_executor.transport_adapter.strip())


def _uses_cli_transport_executor(
    persona_config: Any | None = None,
    settings: Settings | None = None,
) -> bool:
    """Return True when the effective executor is the CLI transport wrapper."""
    executor_cfg = getattr(persona_config, "executor", None)
    adapter_path = str(getattr(executor_cfg, "adapter", "") or "").strip()
    if adapter_path:
        return adapter_path == "ravn.adapters.executors.cli.CliTransportExecutor"
    return _uses_cli_transport_runtime(settings)


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
        # Asking for embeddings and silently getting lexical-only search is the
        # failure this must not have: retrieval keeps working, quality collapses,
        # and nothing says so. Configuration that cannot be honoured is a
        # startup error, not a warning.
        try:
            cls = _import_class(settings.embedding.adapter)
            kwargs = _inject_secrets(
                settings.embedding.kwargs,
                settings.embedding.secret_kwargs_env,
            )
            embedding_port = cls(**kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"embedding.enabled is true but the adapter "
                f"{settings.embedding.adapter!r} could not be constructed: {exc}. "
                f"Set embedding.enabled=false to run with lexical-only search, or "
                f"fix the adapter configuration — memory will not silently degrade."
            ) from exc

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
            recency_floor=settings.memory.recency_floor,
            session_search_truncate_chars=settings.memory.session_search_truncate_chars,
            embedding_port=embedding_port,
            rrf_k=settings.embedding.rrf_k,
            semantic_candidate_limit=settings.embedding.semantic_candidate_limit,
            corpus_stats_interval_seconds=settings.memory.corpus_stats_interval_seconds,
            environment_id=settings.environment.id,
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
        adapter = PostgresMemoryAdapter(dsn=dsn, environment_id=settings.environment.id)

    else:
        # Custom backend via fully-qualified class path. A bad path is a
        # configuration error, not a reason to run on: swallowing it left the
        # resident with memory silently disabled — recording nothing, recalling
        # nothing — and one warning line to explain months of amnesia.
        try:
            cls = _import_class(backend)
        except Exception as exc:
            raise ValueError(
                f"memory.backend {backend!r} is not importable: {type(exc).__name__}: {exc}. "
                "Use 'none' to run without memory deliberately."
            ) from exc
        adapter = cls(path=settings.memory.path)

    return adapter


def _with_mimir_fact_capture(memory: Any | None, mimir: Any | None) -> Any | None:
    """Wire inline-fact capture into the memory port when Mimir is configured.

    The agent used to decide this itself — writing facts to Mimir when it held
    a Mimir adapter and to the memory port otherwise — which put a choice of
    persistence backend inside the agent loop. Composition owns it now, so the
    agent makes one unconditional call.
    """
    if mimir is None:
        return memory
    from ravn.adapters.memory.mimir_facts import MimirFactCapturingMemory  # noqa: PLC0415

    return MimirFactCapturingMemory(mimir, inner=memory)


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


def _mimir_workload_platform_defaults(settings: Settings) -> tuple[str | None, str | None]:
    """Workload-auth fallbacks for Mímir instances from ``gateway.platform``.

    Config-first (``.claude/rules/config-first.md``): when a Mímir instance
    uses ``type: workload`` auth without an explicit ``token_file`` /
    ``exchange_url``, the values configured for the platform tools are the
    canonical source. Returns ``(token_file, exchange_url)`` — either may be
    ``None`` when the platform section is disabled or empty.
    """
    platform = settings.gateway.platform
    if not platform.enabled:
        # A flock persona carries no platform block at all, so this returned
        # (None, None), the Mímir adapter got no exchange_url, and it sent
        # every request to the shared Mímir with no Authorization header —
        # surfacing as a bare 401 that looked like a missing credential rather
        # than missing config. The runtime does advertise both values; prefer
        # them over giving up.
        return _runtime_workload_defaults()
    token_file = platform.workload_token_file or None
    exchange_url = platform.workload_exchange_url or None
    if not exchange_url and platform.base_url:
        exchange_url = f"{platform.base_url.rstrip('/')}/api/v1/tokens/workload/exchange"
    if not exchange_url or not token_file:
        env_token_file, env_exchange_url = _runtime_workload_defaults()
        token_file = token_file or env_token_file
        exchange_url = exchange_url or env_exchange_url
    return token_file, exchange_url


def _runtime_workload_defaults() -> tuple[str | None, str | None]:
    """Workload token file and exchange URL advertised by the runtime.

    Bootstrap wiring injected into every session container, not application
    configuration — the same env the workload identity adapters already read.
    """
    token_file = os.environ.get("NIUU_WORKLOAD_IDENTITY_TOKEN_FILE", "").strip() or None
    exchange_url = os.environ.get(_WORKLOAD_EXCHANGE_URL_ENV, "").strip() or None
    return token_file, exchange_url


def _build_mimir_auth(settings: Settings, auth_config: Any) -> Any:
    """Build a MimirAuth domain object from a MimirAuthConfig section."""
    from ravn.domain.mimir import MimirAuth

    token_file = auth_config.token_file
    exchange_url = auth_config.exchange_url
    if auth_config.type == "workload":
        fallback_token_file, fallback_exchange_url = _mimir_workload_platform_defaults(settings)
        token_file = token_file or fallback_token_file
        exchange_url = exchange_url or fallback_exchange_url
    return MimirAuth(
        type=auth_config.type,
        token=_resolve_mimir_auth_token(auth_config) if auth_config.type == "bearer" else None,
        token_file=token_file,
        exchange_url=exchange_url,
        audiences=tuple(auth_config.audiences),
        trust_domain=auth_config.trust_domain,
    )


def _build_mimir(settings: Settings) -> Any:
    """Build the Mímir adapter from config, or None if disabled."""
    if not settings.mimir.enabled:
        return None

    if settings.mimir.instances:
        from mimir.adapters.markdown import MarkdownMimirAdapter
        from ravn.adapters.mimir.composite import CompositeMimirAdapter
        from ravn.adapters.mimir.http import HttpMimirAdapter
        from ravn.domain.mimir import MimirMount, WriteRouting

        mounts: list[Any] = []
        for inst in settings.mimir.instances:
            if inst.adapter:
                cls = _import_class(inst.adapter)
                port: Any = cls(**_inject_secrets(dict(inst.kwargs), inst.secret_kwargs_env))
            elif inst.path:
                port = MarkdownMimirAdapter(root=inst.path)
            elif inst.url:
                auth = None
                if inst.auth is not None:
                    auth = _build_mimir_auth(settings, inst.auth)
                port = HttpMimirAdapter(
                    base_url=inst.url, auth=auth, environment_id=settings.environment.id
                )
            else:
                # Skipping left the mount silently absent: reads returned fewer
                # results and nothing said a configured instance was missing.
                raise ValueError(
                    f"Mímir instance {inst.name!r} has no adapter, path or url. "
                    f"Give it one, or remove it from mimir.instances."
                )
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
        retry = settings.mimir.read_retry
        return CompositeMimirAdapter(
            mounts=mounts,
            write_routing=routing,
            read_retry_max_seconds=retry.max_seconds,
            read_retry_initial_backoff_seconds=retry.initial_backoff_seconds,
            read_retry_max_backoff_seconds=retry.max_backoff_seconds,
        )

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
