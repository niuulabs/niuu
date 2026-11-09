"""Project a persona onto Claude Code (Agent SDK, CLI subprocess, persistent, sdk-websocket, tmux).

What a persona states and Claude enforces:

* instructions are appended to Claude's own system prompt, never replacing it;
* ``read_only`` runs Claude with ``--tools`` (the built-in tools that cannot change the
  workspace plus the shell), pre-approved rules for exactly those, the read-only repository
  commands, the session's own commands (``present-file``, ``tracker_issue``, ``mimir_*``) and
  the session's MCP servers, and ``dontAsk``, which denies everything else, plus
  ``--strict-mcp-config``;
* ``deny_all`` offers no tool at all and approves nothing.

What Claude cannot do, the projection refuses or states:

* ``prompt`` permission mode: no unattended session can ask an operator per tool call;
* ``workspace_write``: Claude cannot confine a shell or an edit tool to the workspace; the
  session runtime is the boundary. Stated as a note.

Restricting a persona's tools per harness is not available yet, so nothing else in a persona
narrows what Claude is offered.
"""

from __future__ import annotations

from typing import Any

from personas.domain.permission_mode import PermissionMode
from personas.domain.persona_document import PortablePersonaDefinition
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
from skuld.transports.claude_policy import CLOSED_PERMISSION_MODE, ClaudeToolPolicy

__all__ = ["CLOSED_PERMISSION_MODE", "ClaudeProjector"]

#: The built-in tools that cannot change the workspace: reading, the web and the task list.
_READ_ONLY_TOOLS: tuple[str, ...] = (
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskGet",
    "TaskList",
)
_SHELL = "Bash"

#: Commands that inspect a repository without changing it, as the words a shell prefix matches.
_REPOSITORY_READ_COMMANDS: tuple[str, ...] = ("git status", "git diff", "git log", "git show")

#: The commands Skuld puts on the session's PATH (``skuld.transports.tool_shims``) that a
#: read-only session still runs through its shell.
_SESSION_COMMANDS: tuple[str, ...] = (
    "present-file",
    "tracker_issue",
    "mimir_search",
    "mimir_read",
    "mimir_read_source",
    "mimir_related",
    "mimir_list",
    "mimir_write",
    "mimir_ingest",
    "mimir_publish_files",
    "mimir_publish_artifact",
)


def _command_rules(command: str) -> tuple[str, str]:
    """Claude's rules for a command with or without arguments."""
    return (f"Bash({command})", f"Bash({command} *)")


class ClaudeProjector:
    """Instructions and the permission-mode boundary for every Claude transport."""

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
        if policy.mode is PermissionMode.READ_ONLY:
            kwargs["claude_tool_policy"] = self._read_only(context)
        if policy.mode is PermissionMode.DENY_ALL:
            kwargs["claude_tool_policy"] = ClaudeToolPolicy(tools=(), allowed=())
            kwargs["mcp_servers"] = []
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

    @staticmethod
    def _read_only(context: ProjectionContext) -> ClaudeToolPolicy:
        shell_rules: list[str] = []
        for command in (*_REPOSITORY_READ_COMMANDS, *_SESSION_COMMANDS):
            shell_rules.extend(_command_rules(command))
        mcp_rules = [f"mcp__{name}" for name in context.mcp_server_names()]
        return ClaudeToolPolicy(
            tools=(*_READ_ONLY_TOOLS, _SHELL),
            allowed=tuple(dict.fromkeys([*_READ_ONLY_TOOLS, *shell_rules, *mcp_rules])),
        )
