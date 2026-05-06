"""Slash command dispatcher for the Ravn CLI.

Slash commands let users inspect and control the running agent without
breaking the conversational flow.  Every command starts with '/' and is
handled synchronously, returning a formatted string to be printed by the
caller.

Built-in commands
-----------------
/help           — list all slash commands and active tools
/tools          — show loaded tool registry with permission levels
/memory         — show episodic memory summary for this session
/compact        — clear conversation history to reclaim context budget
/budget         — show iteration budget: used / remaining / limit
/todo           — show current todo list
/status         — full agent state dump
/init           — bootstrap a RAVN.md in the current working directory
/checkpoint             — save a checkpoint snapshot now (optional label)
/checkpoint list        — list checkpoints for the current task
/checkpoint restore <id>— restore session to a named checkpoint
/checkpoint delete <id> — delete a named checkpoint

Adding custom commands
----------------------
Implement :class:`~ravn.ports.slash_command.SlashCommandPort` and register
the dotted class path in ``ravn.yaml``::

    slash_commands:
      - adapter: mypackage.commands.VersionCommand
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from ravn.domain.checkpoint import restore_session_from_checkpoint
from ravn.domain.models import Session
from ravn.ports.checkpoint import CheckpointPort
from ravn.ports.slash_command import SlashCommandPort
from ravn.ports.tool import ToolPort

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROJECT_TYPE_SPECS: dict[str, tuple[str, int, list[str]]] = {
    "python": (
        (
            "Python project. Python 3.12+ style preferred.\n"
            "  Run tests with: pytest -x\n"
            "  Lint with: ruff check . && ruff format .\n"
            "  Always type-annotate function signatures.\n"
            "  Use X | None instead of Optional[X]."
        ),
        30,
        ["todo"],
    ),
    "go": (
        (
            "Go project.\n"
            "  Run tests with: go test ./...\n"
            "  Format with: gofmt -w .\n"
            "  Lint with: golangci-lint run\n"
            "  Follow standard Go idioms and error handling patterns."
        ),
        30,
        ["todo"],
    ),
    "node": (
        (
            "Node.js project.\n"
            "  Run tests with: npm test\n"
            "  Lint/format with: npm run lint\n"
            "  Always type-annotate if TypeScript is used."
        ),
        30,
        ["todo"],
    ),
    "rust": (
        (
            "Rust project.\n"
            "  Run tests with: cargo test\n"
            "  Format with: cargo fmt\n"
            "  Lint with: cargo clippy\n"
            "  Prefer idiomatic Rust — use Result/Option, avoid unwrap() in library code."
        ),
        30,
        ["todo"],
    ),
    "generic": (
        "Add project-specific instructions here.\n  Ravn will include these in its system prompt.",
        20,
        [],
    ),
}

_BASE_TOOLS = ["file", "git", "terminal", "web"]

_TODO_STATUS_ICONS: dict[str, str] = {
    "pending": "○",
    "in_progress": "◑",
    "done": "✓",
    "cancelled": "✗",
}


def _build_ravn_template(
    project_name: str,
    notes: str,
    iteration_budget: int = 20,
    extra_tools: list[str] | None = None,
) -> str:
    """Build a RAVN.md file content string from the shared schema."""
    tools = _BASE_TOOLS + (extra_tools or [])
    tools_yaml = "[" + ", ".join(tools) + "]"
    return (
        f"# RAVN Project: {project_name}\n"
        "\n"
        "persona: coding-agent\n"
        f"allowed_tools: {tools_yaml}\n"
        "forbidden_tools: []\n"
        "permission_mode: workspace_write\n"
        "primary_alias: balanced\n"
        "thinking_enabled: false\n"
        f"iteration_budget: {iteration_budget}\n"
        "notes: >\n"
        f"  {notes}\n"
    )


def _detect_project_type(cwd: Path) -> str:
    """Return a project type string by inspecting files in *cwd*."""
    markers: list[tuple[str, list[str]]] = [
        ("python", ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"]),
        ("go", ["go.mod"]),
        ("rust", ["Cargo.toml"]),
        ("node", ["package.json"]),
    ]
    for project_type, files in markers:
        if any((cwd / f).exists() for f in files):
            return project_type
    return "generic"


def _run_async(coro: object) -> object:
    """Run an async coroutine from a synchronous context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result(timeout=30)
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Context dataclass
# ---------------------------------------------------------------------------


@dataclass
class SlashCommandContext:
    """Ambient state forwarded to every slash command handler.

    The caller (typically the CLI REPL) populates this once per REPL
    iteration; individual handlers read what they need.
    """

    session: Session
    tools: list[ToolPort] = field(default_factory=list)
    max_iterations: int = 20
    llm_adapter_name: str = ""
    permission_mode: str = "allow_all"
    cwd: Path | None = None
    # Pre-fetched skill listing: list of (name, description) tuples.
    skills_listing: list[tuple[str, str]] | None = None
    # NIU-537: checkpoint port and task_id for /checkpoint commands.
    checkpoint_port: CheckpointPort | None = None
    task_id: str = ""


# ---------------------------------------------------------------------------
# Built-in command classes
# ---------------------------------------------------------------------------


class HelpCommand(SlashCommandPort):
    """List all registered slash commands."""

    def __init__(self, registry: SlashCommandRegistry) -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "/help"

    @property
    def description(self) -> str:
        return "show this help message"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        return self._registry.help_text()


class ToolsCommand(SlashCommandPort):
    @property
    def name(self) -> str:
        return "/tools"

    @property
    def description(self) -> str:
        return "show loaded tool registry with permission levels"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        if not ctx.tools:
            return "No tools loaded."
        lines = ["Loaded tools:", ""]
        for tool in ctx.tools:
            lines.append(f"  {tool.name:<24} [{tool.required_permission}]  {tool.description}")
        return "\n".join(lines)


class MemoryCommand(SlashCommandPort):
    @property
    def name(self) -> str:
        return "/memory"

    @property
    def description(self) -> str:
        return "show episodic memory summary for this session"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        s = ctx.session
        open_todos = sum(1 for t in s.todos if str(t.status) in ("pending", "in_progress"))
        lines = [
            f"Session ID  : {s.id}",
            f"Turns       : {s.turn_count}",
            f"Messages    : {len(s.messages)}",
            f"Tokens in   : {s.total_usage.input_tokens}",
            f"Tokens out  : {s.total_usage.output_tokens}",
            f"Open todos  : {open_todos}",
        ]
        return "\n".join(lines)


class CompactCommand(SlashCommandPort):
    @property
    def name(self) -> str:
        return "/compact"

    @property
    def description(self) -> str:
        return "clear conversation history to reclaim context budget"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        count = len(ctx.session.messages)
        ctx.session.messages.clear()
        return f"Context compacted: cleared {count} message(s) from conversation history."


class BudgetCommand(SlashCommandPort):
    @property
    def name(self) -> str:
        return "/budget"

    @property
    def description(self) -> str:
        return "show iteration budget: used / remaining / limit"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        used = ctx.session.turn_count
        limit = ctx.max_iterations
        remaining = max(0, limit - used)
        return f"Budget: {used} used / {remaining} remaining / {limit} limit"


class TodoCommand(SlashCommandPort):
    @property
    def name(self) -> str:
        return "/todo"

    @property
    def description(self) -> str:
        return "show current todo list"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        todos = ctx.session.todos
        if not todos:
            return "No todos."
        lines = ["Todos:", ""]
        for item in todos:
            icon = _TODO_STATUS_ICONS.get(str(item.status), "?")
            lines.append(f"  {icon}  [{item.status:<11}]  {item.content}")
        return "\n".join(lines)


class StatusCommand(SlashCommandPort):
    @property
    def name(self) -> str:
        return "/status"

    @property
    def description(self) -> str:
        return "full agent state dump"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        s = ctx.session
        tool_names = [t.name for t in ctx.tools]
        lines = [
            "=== Ravn Agent Status ===",
            "",
            f"Session ID  : {s.id}",
            f"Turns       : {s.turn_count}",
            f"Messages    : {len(s.messages)}",
            f"Tokens in   : {s.total_usage.input_tokens}",
            f"Tokens out  : {s.total_usage.output_tokens}",
            f"Budget      : {s.turn_count} / {ctx.max_iterations}",
            f"LLM adapter : {ctx.llm_adapter_name or '(unknown)'}",
            f"Permission  : {ctx.permission_mode}",
            f"Tools ({len(tool_names):>2}) : {', '.join(tool_names) if tool_names else 'none'}",
            f"Todos       : {len(s.todos)}",
        ]
        return "\n".join(lines)


class SkillsCommand(SlashCommandPort):
    @property
    def name(self) -> str:
        return "/skills"

    @property
    def description(self) -> str:
        return "list all available skills"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        listing = ctx.skills_listing
        if listing is None:
            return (
                "Skill listing not available. "
                "Use the skill_list tool inside the agent to see available skills."
            )
        if not listing:
            return "No skills available. Add .md files to .ravn/skills/ to define skills."
        lines = [f"Skills ({len(listing)}):", ""]
        for name, description in listing:
            lines.append(f"  {name:<24}  {description}")
        return "\n".join(lines)


class InitCommand(SlashCommandPort):
    @property
    def name(self) -> str:
        return "/init"

    @property
    def description(self) -> str:
        return "bootstrap a RAVN.md in the current working directory"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        cwd = ctx.cwd if ctx.cwd is not None else Path.cwd()
        ravn_md = cwd / "RAVN.md"
        if ravn_md.exists():
            return f"RAVN.md already exists at {ravn_md}. Remove it first to re-initialise."
        project_name = cwd.name
        project_type = _detect_project_type(cwd)
        notes, budget, extra_tools = _PROJECT_TYPE_SPECS[project_type]
        content = _build_ravn_template(project_name, notes, budget, extra_tools)
        ravn_md.write_text(content, encoding="utf-8")
        return f"Bootstrapped RAVN.md at {ravn_md} (detected project type: {project_type})"


class PersonaCommand(SlashCommandPort):
    """Manage Ravn personas.

    Subcommands:
        (no args) / create — print guidance message to start persona creation
        list               — list all personas with source and permission mode
        show <name>        — display formatted config for a named persona
        delete <name>      — delete a custom persona file (refuses built-ins)
    """

    @property
    def name(self) -> str:
        return "/persona"

    @property
    def aliases(self) -> list[str]:
        return ["/personas"]

    @property
    def description(self) -> str:
        return "manage personas (list / show / delete / create)"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        sub_parts = args.split(maxsplit=1)
        subcommand = sub_parts[0].lower() if sub_parts else ""
        rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""

        match subcommand:
            case "list":
                return self._list()
            case "show":
                return self._show(rest)
            case "delete":
                return self._delete(rest)
            case "" | "create":
                return (
                    "Describe the persona you want and I'll help you create it.\n\n"
                    "I'll guide you through: role, name, system prompt, tools, "
                    "permissions, LLM settings, and (optionally) pipeline config.\n\n"
                    "Or use '/persona list' to see existing personas, "
                    "'/persona show <name>' to inspect one."
                )
            case _:
                return (
                    f"Unknown subcommand: {subcommand!r}.\n"
                    "Usage: /persona [list | show <name> | delete <name> | create]"
                )

    def _list(self) -> str:
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter  # noqa: PLC0415

        loader = FilesystemPersonaAdapter()
        names = loader.list_names()
        if not names:
            return "No personas found."

        lines = [f"Personas ({len(names)}):", ""]
        for persona_name in names:
            source = loader.source(persona_name)
            persona = loader.load(persona_name)
            perm = persona.permission_mode if persona else ""
            source_display = source if source else "(unknown)"
            perm_display = f"  [{perm}]" if perm else ""
            lines.append(f"  {persona_name:<28} {source_display}{perm_display}")
        return "\n".join(lines)

    def _show(self, name: str) -> str:
        if not name:
            return "Usage: /persona show <name>"

        from ravn.adapters.personas.loader import FilesystemPersonaAdapter  # noqa: PLC0415

        loader = FilesystemPersonaAdapter()
        persona = loader.load(name)
        if persona is None:
            return f"Persona '{name}' not found. Use '/persona list' to see available personas."

        source = loader.source(name)
        lines = [
            f"Persona: {persona.name}",
            f"Source : {source or '(unknown)'}",
            "",
        ]

        if persona.permission_mode:
            lines.append(f"Permission mode : {persona.permission_mode}")
        if persona.iteration_budget:
            lines.append(f"Iteration budget: {persona.iteration_budget}")

        if persona.llm.primary_alias or persona.llm.thinking_enabled or persona.llm.max_tokens:
            llm_parts = []
            if persona.llm.primary_alias:
                llm_parts.append(f"alias={persona.llm.primary_alias}")
            if persona.llm.thinking_enabled:
                llm_parts.append("thinking=true")
            if persona.llm.max_tokens:
                llm_parts.append(f"max_tokens={persona.llm.max_tokens}")
            lines.append(f"LLM             : {', '.join(llm_parts)}")

        if persona.allowed_tools:
            lines.append(f"Allowed tools   : {persona.allowed_tools}")
        if persona.forbidden_tools:
            lines.append(f"Forbidden tools : {persona.forbidden_tools}")

        if persona.produces.event_type:
            lines.append(f"Produces        : {persona.produces.event_type}")
            if persona.produces.schema:
                schema_fields = list(persona.produces.schema)
                lines.append(f"  Schema fields : {schema_fields}")
        if persona.consumes.event_types:
            lines.append(f"Consumes        : {persona.consumes.event_types}")
        if persona.fan_in.contributes_to or persona.fan_in.strategy != "merge":
            lines.append(
                f"Fan-in          : strategy={persona.fan_in.strategy}"
                + (
                    f", contributes_to={persona.fan_in.contributes_to}"
                    if persona.fan_in.contributes_to
                    else ""
                )
            )

        if persona.system_prompt_template:
            preview = persona.system_prompt_template[:200]
            if len(persona.system_prompt_template) > 200:
                preview += "…"
            lines.append("")
            lines.append("System prompt preview:")
            lines.append(f"  {preview}")

        return "\n".join(lines)

    def _delete(self, name: str) -> str:
        if not name:
            return "Usage: /persona delete <name>"

        from ravn.adapters.personas.loader import FilesystemPersonaAdapter  # noqa: PLC0415

        loader = FilesystemPersonaAdapter()

        source = loader.source(name)
        if not source:
            return f"Persona '{name}' not found."

        if source == "[built-in]":
            return (
                f"Cannot delete '{name}': it is a built-in persona. "
                "Built-in personas cannot be removed."
            )

        deleted = loader.delete(name)
        if deleted:
            return f"Persona '{name}' deleted (was at {source})."
        return f"Persona '{name}' could not be deleted."


class CheckpointCommand(SlashCommandPort):
    """Manage session checkpoints.

    Subcommands:
        (no args)          — save a checkpoint now (no label)
        <label>            — save a checkpoint with the given label
        list               — list checkpoints for the current task
        restore <id>       — restore session to a named checkpoint
        delete <id>        — delete a named checkpoint
    """

    @property
    def name(self) -> str:
        return "/checkpoint"

    @property
    def description(self) -> str:
        return "manage session checkpoints (save / list / restore / delete)"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        if ctx.checkpoint_port is None:
            return "Checkpoint port not configured. Enable checkpointing in ravn.yaml."

        sub_parts = args.split(maxsplit=1)
        subcommand = sub_parts[0].lower() if sub_parts else ""

        match subcommand:
            case "list":
                return self._list(ctx)
            case "restore":
                ckpt_id = sub_parts[1].strip() if len(sub_parts) > 1 else ""
                return self._restore(ctx, ckpt_id)
            case "delete":
                ckpt_id = sub_parts[1].strip() if len(sub_parts) > 1 else ""
                return self._delete(ctx, ckpt_id)
            case _:
                return self._save(ctx, label=args.strip())

    def _save(self, ctx: SlashCommandContext, label: str = "") -> str:
        from datetime import UTC, datetime

        from ravn.domain.checkpoint import Checkpoint

        assert ctx.checkpoint_port is not None

        messages = [{"role": m.role, "content": m.content} for m in ctx.session.messages]
        todos = [
            {"id": t.id, "content": t.content, "status": str(t.status), "priority": t.priority}
            for t in ctx.session.todos
        ]
        checkpoint = Checkpoint(
            task_id=ctx.task_id or str(ctx.session.id),
            user_input="",
            messages=messages,
            todos=todos,
            iteration_budget_consumed=0,
            iteration_budget_total=0,
            last_tool_call=None,
            last_tool_result=None,
            partial_response="",
            interrupted_by=None,
            created_at=datetime.now(UTC),
            label=label,
        )
        try:
            checkpoint_id = _run_async(ctx.checkpoint_port.save_snapshot(checkpoint))
        except Exception as exc:
            return f"Checkpoint save failed: {exc}"

        label_note = f" (label: {label!r})" if label else ""
        return f"Checkpoint saved{label_note}.\ncheckpoint_id: {checkpoint_id}"

    def _list(self, ctx: SlashCommandContext) -> str:
        assert ctx.checkpoint_port is not None
        task_id = ctx.task_id or str(ctx.session.id)
        try:
            snapshots = _run_async(ctx.checkpoint_port.list_for_task(task_id))
        except Exception as exc:
            return f"Checkpoint list failed: {exc}"

        if not snapshots:
            return f"No checkpoints found for task {task_id!r}."

        lines = [f"Checkpoints for task {task_id!r} ({len(snapshots)} total):", ""]
        for cp in snapshots:
            label_part = f"  label: {cp.label}" if cp.label else ""
            ts = cp.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            lines.append(f"  {cp.checkpoint_id}  seq={cp.seq}  {ts}")
            if label_part:
                lines.append(f"  {label_part}")
        return "\n".join(lines)

    def _restore(self, ctx: SlashCommandContext, checkpoint_id: str) -> str:
        if not checkpoint_id:
            return "Usage: /checkpoint restore <checkpoint_id>"

        assert ctx.checkpoint_port is not None
        try:
            cp = _run_async(ctx.checkpoint_port.load_snapshot(checkpoint_id))
        except Exception as exc:
            return f"Checkpoint restore failed: {exc}"

        if cp is None:
            return f"Checkpoint {checkpoint_id!r} not found."

        restore_session_from_checkpoint(ctx.session, cp)
        return (
            f"Restored from {checkpoint_id!r}.\n"
            f"  Messages: {len(ctx.session.messages)}\n"
            f"  Todos: {len(ctx.session.todos)}\n"
            f"  Created: {cp.created_at.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )

    def _delete(self, ctx: SlashCommandContext, checkpoint_id: str) -> str:
        if not checkpoint_id:
            return "Usage: /checkpoint delete <checkpoint_id>"

        assert ctx.checkpoint_port is not None
        try:
            _run_async(ctx.checkpoint_port.delete_snapshot(checkpoint_id))
        except Exception as exc:
            return f"Checkpoint delete failed: {exc}"

        return f"Checkpoint {checkpoint_id!r} deleted."


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SlashCommandRegistry:
    """Registry of slash command handlers.

    Built-in commands are pre-registered.  Custom commands implementing
    :class:`~ravn.ports.slash_command.SlashCommandPort` can be added via
    :meth:`register`.  The last registration wins on name collision, so
    custom commands loaded from config always override built-ins.
    """

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommandPort] = {}

    def register(self, cmd: SlashCommandPort) -> None:
        """Register a command (and its aliases) in the registry."""
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def help_text(self) -> str:
        """Return a formatted string listing all registered commands."""
        lines = ["Ravn slash commands:", ""]
        seen: set[int] = set()
        for cmd_name, cmd in sorted(self._commands.items()):
            if id(cmd) in seen:
                continue
            seen.add(id(cmd))
            desc = cmd.description
            lines.append(f"  {cmd_name:<28} — {desc}")
        return "\n".join(lines)

    def handle(self, user_input: str, ctx: SlashCommandContext) -> str | None:
        """Dispatch *user_input* as a slash command.

        Returns the command output string if *user_input* starts with '/'.
        Returns ``None`` if *user_input* is not a slash command.
        """
        stripped = user_input.strip()
        if not stripped.startswith("/"):
            return None

        parts = stripped.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        cmd = self._commands.get(command)
        if cmd is None:
            return f"Unknown command: {command!r}. Type /help for a list of commands."
        return cmd.handle(args, ctx)


# ---------------------------------------------------------------------------
# Default registry (built-in commands pre-registered)
# ---------------------------------------------------------------------------

default_registry = SlashCommandRegistry()
default_registry.register(ToolsCommand())
default_registry.register(MemoryCommand())
default_registry.register(CompactCommand())
default_registry.register(BudgetCommand())
default_registry.register(TodoCommand())
default_registry.register(StatusCommand())
default_registry.register(SkillsCommand())
default_registry.register(InitCommand())
default_registry.register(PersonaCommand())
default_registry.register(CheckpointCommand())
# HelpCommand registered last — it enumerates the registry at call time
default_registry.register(HelpCommand(default_registry))


# ---------------------------------------------------------------------------
# Public dispatcher (backward-compatible entry point)
# ---------------------------------------------------------------------------


def handle(user_input: str, ctx: SlashCommandContext) -> str | None:
    """Dispatch *user_input* as a slash command via the default registry.

    Returns the command output string if *user_input* starts with '/'.
    Returns ``None`` if *user_input* is not a slash command (caller should
    pass it to the agent as a normal message).
    """
    return default_registry.handle(user_input, ctx)
