"""Project a persona onto Claude Code (Agent SDK, CLI subprocess, persistent, sdk-websocket, tmux).

Claude can enforce everything a persona states:

* instructions are appended to Claude's own system prompt, never replacing it;
* a closed persona (``allowed_tools`` listed, or a restricting ``permission_mode``) is run
  with ``--tools`` (the built-in tools the model is offered), pre-approved rules for exactly
  the granted tools and ``dontAsk``, which denies everything else, plus ``--strict-mcp-config``
  with only the MCP servers the persona grants. A persona that only forbids a few tools keeps
  the node's permission mode and has those tools removed (``--disallowedTools``);
* a persona with no built-in tools at all uses the existing MCP-only boundary
  (``read_only_mcp_only`` / ``allowed_mcp_tools``) on the transports that have it, which also
  waits for every granted MCP server to connect before the session starts.

What Claude cannot do, the projection refuses or states:

* ``prompt`` permission mode: no unattended session can ask an operator per tool call;
* a tool forbidden while a granted shell contains it (``git`` with ``shell``): a shell can
  run what it likes, so the restriction is unenforceable;
* ``workspace_write``: Claude cannot confine a shell or an edit tool to the workspace; the
  session runtime is the boundary. Stated as a note.
"""

from __future__ import annotations

from typing import Any

from personas.domain.permission_mode import PermissionMode
from personas.domain.persona_document import PortablePersonaDefinition
from personas.domain.tool_vocabulary import (
    FILE_READ,
    FILE_WRITE,
    GIT,
    GIT_READ,
    PLAN,
    SHELL,
    WEB_FETCH,
    WEB_SEARCH,
    ToolRef,
)
from skuld.personas.permissions import PersonaPolicy
from skuld.personas.projection import (
    NOTE,
    REFUSE,
    HarnessProjection,
    ProjectionContext,
    ProjectionGap,
    build_projection,
    compose_instructions,
)
from skuld.personas.tool_vocabulary import (
    CLAUDE_FILE_READ,
    CLAUDE_FILE_WRITE,
    CLAUDE_PLAN,
    CLAUDE_SHELL,
    CLAUDE_WEB_FETCH,
    CLAUDE_WEB_SEARCH,
    GIT_READ_COMMANDS,
    mcp_servers_for,
    mcp_tool_name,
    shell_commands,
)
from skuld.transports.claude_policy import ClaudeToolPolicy

#: Everything not pre-approved is denied; what makes a closed persona closed.
CLOSED_PERMISSION_MODE = "dontAsk"

_BUILTIN = {
    FILE_READ: CLAUDE_FILE_READ,
    FILE_WRITE: CLAUDE_FILE_WRITE,
    WEB_FETCH: (CLAUDE_WEB_FETCH,),
    WEB_SEARCH: (CLAUDE_WEB_SEARCH,),
    PLAN: CLAUDE_PLAN,
}


def _bash(command: str) -> tuple[str, str]:
    """Claude's rules for a command with or without arguments."""
    return (f"Bash({command})", f"Bash({command} *)")


def _mcp_rule(server: str, tool: str = "") -> str:
    return f"mcp__{server}__{tool}" if tool else f"mcp__{server}"


def _dedupe(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


class ClaudeProjector:
    """Instructions, tool boundary and permission mode for every Claude transport."""

    engine = "claude"

    def project(
        self, persona: PortablePersonaDefinition, context: ProjectionContext
    ) -> HarnessProjection:
        policy = PersonaPolicy.from_definition(persona.id, persona.definition)
        gaps: list[ProjectionGap] = []
        kwargs: dict[str, Any] = {
            "system_prompt": compose_instructions(persona.definition, context.request_prompt)
        }
        self._check_mode(policy, gaps)
        for ref in policy.carve_outs():
            gaps.append(
                ProjectionGap(
                    REFUSE,
                    f"forbidden_tools:{ref.text}",
                    f"{ref.text} is forbidden while shell is granted; a shell runs it anyway, "
                    "so the restriction cannot be enforced (forbid shell too, or drop "
                    f"{ref.text} from forbidden_tools)",
                )
            )
        if policy.restricted:
            kwargs.update(self._boundary(policy, context, gaps))
        return build_projection(self.engine, persona, context, kwargs, gaps)

    @staticmethod
    def _check_mode(policy: PersonaPolicy, gaps: list[ProjectionGap]) -> None:
        if policy.mode is PermissionMode.PROMPT:
            gaps.append(
                ProjectionGap(
                    REFUSE,
                    "permission_mode:prompt",
                    "no unattended Claude session can ask an operator before each tool call",
                )
            )
        if policy.mode is PermissionMode.WORKSPACE_WRITE:
            gaps.append(
                ProjectionGap(
                    NOTE,
                    "permission_mode:workspace_write",
                    "Claude cannot confine a shell or an edit tool to the workspace; the "
                    "session runtime is the boundary",
                )
            )

    def _boundary(
        self, policy: PersonaPolicy, context: ProjectionContext, gaps: list[ProjectionGap]
    ) -> dict[str, Any]:
        names = context.mcp_server_names()
        kept, allow_mcp, deny_mcp = self._mcp(policy, context, names, gaps)
        if not (policy.closed or policy.restricts_mode):
            # Only a few tools are forbidden: leave the node's mode and the rest as they are
            # and remove those tools from the model's context.
            return {
                "mcp_servers": kept,
                "claude_tool_policy": ClaudeToolPolicy(
                    disallowed=_dedupe([*self._denied_builtins(policy), *deny_mcp]),
                    permission_mode=None,
                ),
            }
        tools, allow = self._builtins(policy, context, gaps)
        if policy.mode is PermissionMode.DENY_ALL:
            tools, allow, allow_mcp, kept = [], [], [], []
        if not tools and context.supports_read_only_mcp_boundary and not _is_deny_all(policy):
            if policy.grants(ToolRef("builtin", PLAN)):
                gaps.append(
                    ProjectionGap(
                        NOTE,
                        "allowed_tools:plan",
                        "the MCP-only boundary offers no built-in tools, so no task list",
                    )
                )
            return {
                "mcp_servers": kept,
                "read_only_mcp_only": True,
                "allowed_mcp_tools": list(_dedupe(allow_mcp)),
            }
        return {
            "mcp_servers": kept,
            "claude_tool_policy": ClaudeToolPolicy(
                tools=_dedupe(tools),
                allowed=_dedupe([*allow, *allow_mcp]),
                # A closed persona's built-in tools are already limited by ``--tools``; a deny
                # rule on ``Bash`` would also deny the commands it is granted.
                disallowed=_dedupe(deny_mcp),
                permission_mode=CLOSED_PERMISSION_MODE,
            ),
        }

    @staticmethod
    def _denied_builtins(policy: PersonaPolicy) -> list[str]:
        deny: list[str] = []
        for ref in policy.forbidden:
            if ref.kind != "builtin" or ref.name == PLAN:
                continue
            deny.extend(_BUILTIN.get(ref.name, ()))
            if ref.name == SHELL:
                deny.append(CLAUDE_SHELL)
        return deny

    def _builtins(
        self, policy: PersonaPolicy, context: ProjectionContext, gaps: list[ProjectionGap]
    ) -> tuple[list[str], list[str]]:
        tools: list[str] = []
        allow: list[str] = []
        for name, claude_names in _BUILTIN.items():
            ref = ToolRef("builtin", name)
            if not policy.grants(ref) or policy.mode_removes(ref):
                self._note_if_removed(policy, ref, gaps)
                continue
            tools.extend(claude_names)
            allow.extend(claude_names)
        self._note_if_removed(policy, ToolRef("builtin", GIT), gaps)
        shell = ToolRef("builtin", SHELL)
        if policy.grants(shell) and not policy.mode_removes(shell):
            tools.append(CLAUDE_SHELL)
            allow.append(CLAUDE_SHELL)
            return tools, allow
        self._note_if_removed(policy, shell, gaps)
        patterns = self._shell_patterns(policy)
        if patterns:
            tools.append(CLAUDE_SHELL)
            allow.extend(patterns)
        return tools, allow

    @staticmethod
    def _note_if_removed(policy: PersonaPolicy, ref: ToolRef, gaps: list[ProjectionGap]) -> None:
        if policy.mode_removes(ref) and policy.lists(ref) and policy.closed:
            gaps.append(
                ProjectionGap(
                    NOTE,
                    f"allowed_tools:{ref.text}",
                    f"{ref.text} is listed but permission_mode read_only takes it away",
                )
            )

    @staticmethod
    def _shell_patterns(policy: PersonaPolicy) -> list[str]:
        """Rules letting a shell run only the commands the persona is granted."""
        patterns: list[str] = []
        if policy.closed:
            candidates = [ref for ref in policy.allowed if ref.kind in ("builtin", "capability")]
        else:
            candidates = [
                ToolRef("builtin", GIT),
                ToolRef("builtin", GIT_READ),
                ToolRef("capability", "present_file"),
                ToolRef("capability", "tracker"),
                ToolRef("capability", "mimir"),
            ]
        for ref in candidates:
            if ref.kind == "builtin" and ref.name not in (GIT, GIT_READ):
                continue
            if policy.forbids(ref) or policy.mode_removes(ref) or not policy.grants_command(ref):
                continue
            if ref.kind == "builtin" and ref.name == GIT:
                patterns.extend(_bash("git"))
                continue
            commands = GIT_READ_COMMANDS if ref.name == GIT_READ else shell_commands(ref)
            for command in commands:
                patterns.extend(_bash(command))
        return patterns

    @staticmethod
    def _mcp(
        policy: PersonaPolicy,
        context: ProjectionContext,
        names: list[str],
        gaps: list[ProjectionGap],
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """Servers to keep, their pre-approved rules and the rules that deny forbidden ones."""
        allow: list[str] = []
        deny: list[str] = []
        keep: set[str] = {name for name in context.platform_mcp_servers if name in names}
        allow.extend(_mcp_rule(name) for name in sorted(keep))
        for ref in policy.forbidden:
            for server in mcp_servers_for(ref, names):
                deny.append(_mcp_rule(server, mcp_tool_name(ref)))
        if not policy.closed:
            for name in names:
                whole = ToolRef("mcp", name)
                if not policy.forbids(whole):
                    keep.add(name)
                    allow.append(_mcp_rule(name))
        for ref in policy.allowed:
            if ref.kind == "builtin":
                continue
            servers = mcp_servers_for(ref, names)
            if not servers:
                # A PATH command (present-file, tracker_issue, mimir_*) serves the capability
                # without a server; anything else needs the server it names.
                if not shell_commands(ref):
                    gaps.append(
                        ProjectionGap(
                            NOTE,
                            f"allowed_tools:{ref.text}",
                            "no such MCP server is configured in this session",
                        )
                    )
                continue
            for server in servers:
                if policy.forbids(ToolRef("mcp", server)):
                    continue
                keep.add(server)
                allow.append(_mcp_rule(server, mcp_tool_name(ref)))
        servers_kept = [dict(s) for s in context.mcp_servers if str(s.get("name")) in keep]
        return servers_kept, allow, deny


def _is_deny_all(policy: PersonaPolicy) -> bool:
    return policy.mode is PermissionMode.DENY_ALL
