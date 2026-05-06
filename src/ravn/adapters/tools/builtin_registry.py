"""Built-in tool registry — maps tool names to dynamic adapter definitions.

Each entry in ``BUILTIN_TOOLS`` describes how to construct a built-in tool
from settings and runtime context.  The composition root uses this registry
instead of hardcoded imports, so adding a new built-in tool is config-only.

Groups
------
``core``      — file, git, bash, web_fetch, todo, ask_user, terminal
``extended``  — web_search, introspection, memory search, session search
``skill``     — skill_list, skill_run  (conditional on settings.skill.enabled)
``platform``  — volundr/tyr platform tools  (conditional on gateway.platform.enabled)
``cascade``   — marker group; cascade tools are wired externally via build_cascade_tools()

Runtime context keys
--------------------
workspace       : Path — workspace root directory
session         : Session — current agent session
llm             : LLMPort — active LLM adapter
memory          : MemoryPort | None — active memory adapter (may be None)
iteration_budget: IterationBudget | None — active budget tracker
persona_prefix  : str — first 40 chars of persona system_prompt_template (or "")
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ravn.config import Settings


@dataclass
class BuiltinToolDef:
    """Definition of a single built-in tool in the registry."""

    adapter: str
    """Fully-qualified class path for the tool adapter (passed to _import_class)."""

    groups: frozenset[str]
    """Logical group names this tool belongs to (core / extended / skill / platform)."""

    kwargs_fn: Callable[[Settings, dict[str, Any]], dict[str, Any]] = field(
        default_factory=lambda: lambda _s, _ctx: {}
    )
    """Returns the constructor kwargs dict given settings and runtime context."""

    required_context: frozenset[str] = field(default_factory=frozenset)
    """Runtime context keys that must be non-None; tool is skipped when any is absent."""

    condition: Callable[[Settings], bool] | None = None
    """Optional settings-based gate; tool is skipped when it returns False."""


# ---------------------------------------------------------------------------
# Internal helpers (called from kwargs_fn lambdas)
# ---------------------------------------------------------------------------


def _build_skill_port(settings: Settings, workspace: Any) -> Any:
    """Construct the skill backend adapter from config."""
    if settings.skill.backend == "sqlite":
        from ravn.adapters.skill.sqlite import SqliteSkillAdapter  # noqa: PLC0415

        return SqliteSkillAdapter(
            path=settings.skill.path,
            suggestion_threshold=settings.skill.suggestion_threshold,
            cache_max_entries=settings.skill.cache_max_entries,
        )
    from ravn.adapters.skill.file_registry import FileSkillRegistry  # noqa: PLC0415

    return FileSkillRegistry(
        skill_dirs=settings.skill.skill_dirs or None,
        include_builtin=settings.skill.include_builtin,
        cwd=workspace,
    )


def _build_web_search_kwargs(settings: Settings, _ctx: dict[str, Any]) -> dict[str, Any]:
    """Construct kwargs for WebSearchTool, including the dynamic provider."""
    from ravn.cli.commands import _import_class, _inject_secrets  # noqa: PLC0415

    ws_cfg = settings.tools.web.search
    provider = None
    mock_path = "ravn.adapters.tools.web_search.MockWebSearchProvider"
    if ws_cfg.provider.adapter != mock_path:
        import logging  # noqa: PLC0415

        logger = logging.getLogger(__name__)
        try:
            cls = _import_class(ws_cfg.provider.adapter)
            merged = _inject_secrets(
                dict(ws_cfg.provider.kwargs),
                ws_cfg.provider.secret_kwargs_env,
            )
            provider = cls(**merged)
        except Exception as exc:
            logger.warning("Failed to load web search provider: %s — using mock", exc)
    return {"provider": provider, "num_results": ws_cfg.num_results}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BUILTIN_TOOLS: dict[str, BuiltinToolDef] = {
    # =========================================================================
    # core — always present in every profile
    # =========================================================================
    "read_file": BuiltinToolDef(
        adapter="ravn.adapters.tools.file_tools.ReadFileTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {
            "workspace": ctx["workspace"],
            "max_bytes": s.tools.file.max_read_bytes,
        },
    ),
    "write_file": BuiltinToolDef(
        adapter="ravn.adapters.tools.file_tools.WriteFileTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {
            "workspace": ctx["workspace"],
            "max_bytes": s.tools.file.max_write_bytes,
            "binary_check_bytes": s.tools.file.binary_check_bytes,
        },
    ),
    "edit_file": BuiltinToolDef(
        adapter="ravn.adapters.tools.file_tools.EditFileTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {
            "workspace": ctx["workspace"],
            "max_bytes": s.tools.file.max_write_bytes,
            "binary_check_bytes": s.tools.file.binary_check_bytes,
        },
    ),
    "glob_search": BuiltinToolDef(
        adapter="ravn.adapters.tools.file_tools.GlobSearchTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {"workspace": ctx["workspace"]},
    ),
    "grep_search": BuiltinToolDef(
        adapter="ravn.adapters.tools.file_tools.GrepSearchTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {"workspace": ctx["workspace"]},
    ),
    "git_status": BuiltinToolDef(
        adapter="ravn.adapters.tools.git.GitStatusTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {"workspace": ctx["workspace"]},
    ),
    "git_diff": BuiltinToolDef(
        adapter="ravn.adapters.tools.git.GitDiffTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {"workspace": ctx["workspace"]},
    ),
    "git_add": BuiltinToolDef(
        adapter="ravn.adapters.tools.git.GitAddTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {"workspace": ctx["workspace"]},
    ),
    "git_commit": BuiltinToolDef(
        adapter="ravn.adapters.tools.git.GitCommitTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {"workspace": ctx["workspace"]},
    ),
    "git_checkout": BuiltinToolDef(
        adapter="ravn.adapters.tools.git.GitCheckoutTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {"workspace": ctx["workspace"]},
    ),
    "git_log": BuiltinToolDef(
        adapter="ravn.adapters.tools.git.GitLogTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {"workspace": ctx["workspace"]},
    ),
    "git_pr": BuiltinToolDef(
        adapter="ravn.adapters.tools.git.GitPrTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {"workspace": ctx["workspace"]},
    ),
    "bash": BuiltinToolDef(
        adapter="ravn.adapters.tools.bash.BashTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {
            "config": s.tools.bash,
            "workspace_root": ctx["workspace"],
        },
    ),
    "web_fetch": BuiltinToolDef(
        adapter="ravn.adapters.tools.web_fetch.WebFetchTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {
            "timeout": s.tools.web.fetch.timeout,
            "user_agent": s.tools.web.fetch.user_agent,
            "content_budget": s.tools.web.fetch.content_budget,
        },
    ),
    "todo_write": BuiltinToolDef(
        adapter="ravn.adapters.tools.todo.TodoWriteTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {"session": ctx["session"]},
    ),
    "todo_read": BuiltinToolDef(
        adapter="ravn.adapters.tools.todo.TodoReadTool",
        groups=frozenset({"core"}),
        kwargs_fn=lambda s, ctx: {"session": ctx["session"]},
    ),
    "ask_user": BuiltinToolDef(
        adapter="ravn.adapters.tools.ask_user.AskUserTool",
        groups=frozenset({"core"}),
    ),
    # Terminal — local backend (default)
    "terminal": BuiltinToolDef(
        adapter="ravn.adapters.tools.terminal.TerminalTool",
        groups=frozenset({"core"}),
        condition=lambda s: s.tools.terminal.backend != "docker",
        kwargs_fn=lambda s, ctx: {
            "shell": s.tools.terminal.shell,
            "timeout_seconds": s.tools.terminal.timeout_seconds,
            "persistent_shell": s.tools.terminal.persistent_shell,
        },
    ),
    # Terminal — docker backend
    "terminal_docker": BuiltinToolDef(
        adapter="ravn.adapters.tools.terminal_docker.DockerTerminalTool",
        groups=frozenset({"core"}),
        condition=lambda s: s.tools.terminal.backend == "docker",
        kwargs_fn=lambda s, ctx: {
            "config": s.tools.terminal.docker,
            "workspace_root": ctx["workspace"],
            "timeout_seconds": s.tools.terminal.timeout_seconds,
        },
    ),
    # =========================================================================
    # extended — additional capabilities
    # =========================================================================
    "web_search": BuiltinToolDef(
        adapter="ravn.adapters.tools.web_search.WebSearchTool",
        groups=frozenset({"extended"}),
        kwargs_fn=_build_web_search_kwargs,
    ),
    "ravn_state": BuiltinToolDef(
        adapter="ravn.adapters.tools.introspection.RavnStateTool",
        groups=frozenset({"extended"}),
        kwargs_fn=lambda s, ctx: {
            "tool_names": [],  # populated after filtering in _build_tools()
            "permission_mode": s.permission.mode,
            "model": s.effective_model(),
            "persona": ctx.get("persona_prefix", ""),
            "iteration_budget": ctx.get("iteration_budget"),
            "memory": ctx.get("memory"),
            "discovery": ctx.get("discovery"),
        },
    ),
    "ravn_reflect": BuiltinToolDef(
        adapter="ravn.adapters.tools.introspection.RavnReflectTool",
        groups=frozenset({"extended"}),
        kwargs_fn=lambda s, ctx: {
            "llm": ctx["llm"],
            "session": ctx["session"],
            "model": s.memory.reflection_model,
            "max_tokens": s.memory.reflection_max_tokens,
        },
    ),
    "ravn_memory_search": BuiltinToolDef(
        adapter="ravn.adapters.tools.introspection.RavnMemorySearchTool",
        groups=frozenset({"extended"}),
        required_context=frozenset({"memory"}),
        kwargs_fn=lambda s, ctx: {"memory": ctx["memory"]},
    ),
    "session_search": BuiltinToolDef(
        adapter="ravn.adapters.tools.session_search.SessionSearchTool",
        groups=frozenset({"extended"}),
        required_context=frozenset({"memory"}),
        kwargs_fn=lambda s, ctx: {"memory": ctx["memory"]},
    ),
    # =========================================================================
    # skill — skill discovery and execution
    # =========================================================================
    "skill_list": BuiltinToolDef(
        adapter="ravn.adapters.tools.skill_tools.SkillListTool",
        groups=frozenset({"skill"}),
        condition=lambda s: s.skill.enabled,
        required_context=frozenset({"skill_port"}),
        kwargs_fn=lambda s, ctx: {"skill_port": ctx["skill_port"]},
    ),
    "skill_run": BuiltinToolDef(
        adapter="ravn.adapters.tools.skill_tools.SkillRunTool",
        groups=frozenset({"skill"}),
        condition=lambda s: s.skill.enabled,
        required_context=frozenset({"skill_port"}),
        kwargs_fn=lambda s, ctx: {"skill_port": ctx["skill_port"]},
    ),
    # =========================================================================
    # platform — Niuu platform integration (conditional on gateway.platform.enabled)
    # =========================================================================
    "volundr_session": BuiltinToolDef(
        adapter="ravn.adapters.tools.platform_tools.VolundrSessionTool",
        groups=frozenset({"platform"}),
        condition=lambda s: s.gateway.platform.enabled,
        kwargs_fn=lambda s, ctx: {
            "base_url": s.gateway.platform.base_url,
            "timeout": s.gateway.platform.timeout,
            "pat_token": s.gateway.platform.pat_token,
        },
    ),
    "volundr_git": BuiltinToolDef(
        adapter="ravn.adapters.tools.platform_tools.VolundrGitTool",
        groups=frozenset({"platform"}),
        condition=lambda s: s.gateway.platform.enabled,
        kwargs_fn=lambda s, ctx: {
            "base_url": s.gateway.platform.base_url,
            "timeout": s.gateway.platform.timeout,
            "pat_token": s.gateway.platform.pat_token,
        },
    ),
    "tyr_saga": BuiltinToolDef(
        adapter="ravn.adapters.tools.platform_tools.TyrSagaTool",
        groups=frozenset({"platform"}),
        condition=lambda s: s.gateway.platform.enabled,
        kwargs_fn=lambda s, ctx: {
            "base_url": s.gateway.platform.base_url,
            "timeout": s.gateway.platform.timeout,
            "pat_token": s.gateway.platform.pat_token,
        },
    ),
    "tracker_issue": BuiltinToolDef(
        adapter="ravn.adapters.tools.platform_tools.TrackerIssueTool",
        groups=frozenset({"platform"}),
        condition=lambda s: s.gateway.platform.enabled,
        kwargs_fn=lambda s, ctx: {
            "base_url": s.gateway.platform.base_url,
            "timeout": s.gateway.platform.timeout,
            "pat_token": s.gateway.platform.pat_token,
        },
    ),
    # =========================================================================
    # ravn — persona management tools
    # =========================================================================
    "persona_validate": BuiltinToolDef(
        adapter="ravn.adapters.tools.persona_tools.PersonaValidateTool",
        groups=frozenset({"ravn"}),
    ),
    "persona_save": BuiltinToolDef(
        adapter="ravn.adapters.tools.persona_tools.PersonaSaveTool",
        groups=frozenset({"ravn"}),
    ),
}
