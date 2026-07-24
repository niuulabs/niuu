"""Tool, hook, compression, and prompt builders for the Ravn CLI."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ravn.agent import PostToolHook, PreToolHook
from ravn.cli.runtime_builders import (
    _build_workflow_capability_sources,
    _get_tool_group,
    _import_class,
    _inject_secrets,
    _resident_ravn_state_dir,
)
from ravn.config import Settings, resolve_trust_tools
from ravn.domain.models import Session, ToolCall, ToolResult

logger = logging.getLogger(__name__)


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
    session_join_manager: Any | None = None,
    permission: Any | None = None,
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
        "permission": permission,
    }
    runtime_ctx["capability_tools_provider"] = lambda: runtime_ctx.get("capability_tools", [])

    # Learned tools: 'dispatch' (default) keeps them out of the per-turn tool
    # schema — capability_list enumerates the artifact catalog and the single
    # learned_tool_run tool executes by name. 'bulk' is the legacy path that
    # loads every artifact as a native callable (NIU-1118).
    dispatch_learned_tools = settings.resident_evolution.learned_tool_injection_mode == "dispatch"
    if dispatch_learned_tools:
        resolver = _build_learned_tool_resolver(settings, workspace)
        if resolver is not None:
            runtime_ctx["learned_tool_resolver"] = resolver
            runtime_ctx["learned_tools_provider"] = resolver.list_artifacts

    # Pre-build shared skill port so both skill_list and skill_run reuse one instance
    if "skill" in include_groups and settings.skill.enabled:
        from ravn.adapters.tools.builtin_registry import _build_skill_port  # noqa: PLC0415

        runtime_ctx["skill_port"] = _build_skill_port(settings, workspace)

    if "workflow" in include_groups:
        workflow_sources = _build_workflow_capability_sources(settings)
        if workflow_sources:
            runtime_ctx["workflow_sources"] = workflow_sources

    if settings.gateway.platform.enabled and include_groups & {"ravn", "a2a"}:
        from ravn.adapters.agent_directory import (  # noqa: PLC0415
            GuildAgentDirectoryAdapter,
        )
        from ravn.adapters.tool_build.http import (  # noqa: PLC0415
            client_from_workload_identity,
        )

        platform = settings.gateway.platform
        peer_client = client_from_workload_identity(
            base_url=platform.base_url,
            external_token=platform.pat_token,
            workload_token_file=platform.workload_token_file,
            workload_exchange_url=platform.workload_exchange_url,
            workload_audiences=platform.workload_audiences,
            timeout_seconds=platform.timeout,
            allowed_origins=[platform.base_url, *platform.a2a_trusted_origins],
        )
        runtime_ctx["agent_directory"] = GuildAgentDirectoryAdapter(
            base_url=platform.base_url,
            client=peer_client,
            agent_card_urls=platform.a2a_agent_card_urls,
        )
        runtime_ctx["a2a_client"] = peer_client
        runtime_ctx["a2a_trusted_origins"] = [
            platform.base_url,
            *platform.a2a_trusted_origins,
        ]

    # The session_join tool only makes sense for a resident daemon, which owns
    # the manager and injects it here; when absent (CLI single-shot) the tool
    # is filtered out via its required_context.
    if session_join_manager is not None:
        runtime_ctx["session_join_manager"] = session_join_manager

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

    if not dispatch_learned_tools:
        tools.extend(_load_resident_learned_tools(settings, workspace, tools))

    # -- Apply enabled/disabled filters --
    tools = _filter_tools(tools, settings, persona_config)

    # Update state tool with final tool names after filtering
    # Keep the provider on the returned list itself. CLI transports expose it
    # through a long-lived MCP server, where build_tool can register a freshly
    # verified tool after server construction.
    runtime_ctx["capability_tools"] = tools
    if state_tool is not None:
        state_tool._tool_names = [t.name for t in tools]

    return tools


def _build_learned_tool_resolver(settings: Settings, workspace: Path) -> Any | None:
    """Construct the on-demand learned-tool resolver, sweeping stale venvs."""
    from ravn.valkyrie_evolution.learned_tools import (  # noqa: PLC0415
        LearnedToolError,
        LearnedToolResolver,
    )

    try:
        resolver = LearnedToolResolver(
            state_dir=_resident_ravn_state_dir(workspace, settings),
            execution_backend=settings.resident_evolution.learned_tool_execution_backend,
            execution_backend_kwargs=(
                settings.resident_evolution.learned_tool_k8s.model_dump()
                if settings.resident_evolution.learned_tool_execution_backend == "k8s_job"
                else None
            ),
            workspace_root=workspace,
            timeout_seconds=settings.resident_evolution.tool_timeout_seconds,
        )
    except LearnedToolError as exc:
        logger.warning("Skipping learned tools: %s", exc)
        return None
    resolver.sweep_orphaned_venvs()
    from ravn.tool_observability import publish_learned_tool_inventory  # noqa: PLC0415

    publish_learned_tool_inventory(resolver.list_artifacts())
    return resolver


def _load_resident_learned_tools(
    settings: Settings,
    workspace: Path,
    existing_tools: list[Any],
) -> list[Any]:
    """Legacy bulk mode: load every learned tool as a native callable.

    Kept behind ``resident_evolution.learned_tool_injection_mode: bulk`` — the
    default dispatch mode exposes learned tools through capability_list +
    learned_tool_run instead, so the prompt does not grow with the catalog.
    """
    resolver = _build_learned_tool_resolver(settings, workspace)
    if resolver is None:
        return []
    seen = {tool.name for tool in existing_tools}
    loaded: list[Any] = []
    for artifact in resolver.list_artifacts():
        name = artifact.manifest.name
        if name in seen:
            continue
        try:
            tool = resolver.load(name)
        except Exception as exc:
            logger.warning("Failed to load learned tool %s: %s", name, exc)
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
# Some groups do not use prefix naming; for example, "file" maps to
# "read_file" instead of "file_read".
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
    "a2a": ["a2a_task"],
    "cascade": ["cascade_delegate", "cascade_broadcast"],
    "volundr": ["volundr_session", "volundr_git"],
    "ravn": [
        "persona_validate",
        "persona_save",
        "skill_list",
        "skill_run",
        "skill_manage",
        "capability_list",
        "a2a_task",
        "learned_tool_run",
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
