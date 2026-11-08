"""Project a persona onto Codex (app-server and exec).

What Codex can enforce:

* instructions ride as ``developerInstructions`` (app-server) or ``developer_instructions``
  (exec), additive to Codex's own prompt, and are sent again on every start and resume;
* ``read_only`` / ``workspace_write`` become Codex's OS sandbox (``read-only`` /
  ``workspace-write``) with approvals ``never``. The sandbox also stops a persona with no
  ``file_write`` from changing files through the shell or ``apply_patch``;
* the shell tool is switched off when the persona does not grant it, and hosted web search is
  switched off when it grants neither web tool;
* MCP servers are isolated to the ones the persona grants (app-server only: it disables
  account connectors, plugins and tool suggestions and verifies each server connected).

What Codex cannot do, so the projection refuses it:

* ``prompt`` and ``deny_all`` permission modes;
* a persona that grants file reading or git without the shell: Codex reads files and runs git
  only through its shell tool;
* restricting MCP servers on exec, which cannot isolate them from the user's own Codex config.

Codex has no separate web-fetch tool; web search stands in for it, stated as a note. The
sandbox is only ever narrowed against the node's own configuration, never widened.
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
from skuld.personas.tool_vocabulary import mcp_servers_for, shell_commands

#: Codex sandbox modes, narrowest first.
SANDBOX_RANK: dict[str, int] = {"read-only": 0, "workspace-write": 1, "danger-full-access": 2}
UNATTENDED_APPROVAL = "never"


def narrower_sandbox(node: str, persona: str) -> str:
    """The narrower of the node's sandbox and the persona's. ``node`` empty means unset."""
    if not node:
        return persona
    return min(node, persona, key=SANDBOX_RANK.__getitem__)


class CodexProjector:
    """Instructions, sandbox, shell, web and MCP isolation for Codex transports."""

    engine = "codex"

    def project(
        self, persona: PortablePersonaDefinition, context: ProjectionContext
    ) -> HarnessProjection:
        policy = PersonaPolicy.from_definition(persona.id, persona.definition)
        gaps: list[ProjectionGap] = []
        kwargs: dict[str, Any] = {
            "system_prompt": compose_instructions(persona.definition, context.request_prompt)
        }
        self._permissions(policy, context, kwargs, gaps)
        if policy.restricts_tools:
            self._tools(policy, context, kwargs, gaps)
        return build_projection(self.engine, persona, context, kwargs, gaps)

    @staticmethod
    def _permissions(
        policy: PersonaPolicy,
        context: ProjectionContext,
        kwargs: dict[str, Any],
        gaps: list[ProjectionGap],
    ) -> None:
        if policy.mode in (PermissionMode.PROMPT, PermissionMode.DENY_ALL):
            gaps.append(
                ProjectionGap(
                    REFUSE,
                    f"permission_mode:{policy.mode.value}",
                    "Codex cannot ask an operator per action in an unattended session and has "
                    "no mode that denies every tool",
                )
            )
            return
        wanted = (
            {
                PermissionMode.READ_ONLY: "read-only",
                PermissionMode.WORKSPACE_WRITE: "workspace-write",
            }.get(policy.mode)
            if policy.mode
            else None
        )
        file_write = ToolRef("builtin", FILE_WRITE)
        if policy.restricts_tools and not policy.grants(file_write):
            if wanted != "read-only":
                gaps.append(
                    ProjectionGap(
                        NOTE,
                        "allowed_tools:file_write",
                        "the persona has no file_write, so Codex runs in its read-only sandbox "
                        "and neither apply_patch nor the shell can change files",
                    )
                )
            wanted = "read-only"
        if wanted is None:
            return
        node = "danger-full-access" if context.node_skip_permissions else context.node_sandbox
        if node and node not in SANDBOX_RANK:
            gaps.append(
                ProjectionGap(
                    REFUSE,
                    "node:sandbox",
                    f"the node's sandbox {node!r} is not a Codex sandbox mode "
                    f"({', '.join(SANDBOX_RANK)}); fix the node's sandbox setting",
                )
            )
            return
        kwargs["skip_permissions"] = False
        kwargs["sandbox"] = narrower_sandbox(node, wanted)
        kwargs["approval_policy"] = context.node_approval_policy or UNATTENDED_APPROVAL

    def _tools(
        self,
        policy: PersonaPolicy,
        context: ProjectionContext,
        kwargs: dict[str, Any],
        gaps: list[ProjectionGap],
    ) -> None:
        for ref in policy.carve_outs():
            gaps.append(
                ProjectionGap(
                    REFUSE,
                    f"forbidden_tools:{ref.text}",
                    f"{ref.text} is forbidden while shell is granted; Codex's shell runs it "
                    "anyway, so the restriction cannot be enforced",
                )
            )
        self._shell(policy, context, kwargs, gaps)
        self._web(policy, kwargs, gaps)
        self._mcp(policy, context, kwargs, gaps)

    @staticmethod
    def _shell(
        policy: PersonaPolicy,
        context: ProjectionContext,
        kwargs: dict[str, Any],
        gaps: list[ProjectionGap],
    ) -> None:
        if policy.shell_granted():
            return
        needs_shell = [
            name for name in (FILE_READ, GIT, GIT_READ) if policy.grants(ToolRef("builtin", name))
        ]
        if needs_shell:
            gaps.append(
                ProjectionGap(
                    REFUSE,
                    f"allowed_tools:{','.join(needs_shell)}",
                    "Codex reads files and runs git only through its shell tool and the "
                    "persona does not grant shell",
                )
            )
        kwargs["shell_tool_enabled"] = False
        names = context.mcp_server_names()
        for ref in policy.allowed:
            if ref.kind != "capability" or not shell_commands(ref):
                continue
            if not mcp_servers_for(ref, names):
                gaps.append(
                    ProjectionGap(
                        NOTE,
                        f"allowed_tools:{ref.text}",
                        "Codex reaches this only through the shell, which the persona does "
                        "not grant, and the session has no MCP server for it",
                    )
                )

    @staticmethod
    def _web(policy: PersonaPolicy, kwargs: dict[str, Any], gaps: list[ProjectionGap]) -> None:
        fetch = policy.grants(ToolRef("builtin", WEB_FETCH))
        search = policy.grants(ToolRef("builtin", WEB_SEARCH))
        if not fetch and not search:
            kwargs["web_search_enabled"] = False
            return
        if fetch != search:
            gaps.append(
                ProjectionGap(
                    NOTE,
                    "allowed_tools:web_fetch,web_search",
                    "Codex has one hosted web tool, web search, which serves both",
                )
            )

    @staticmethod
    def _mcp(
        policy: PersonaPolicy,
        context: ProjectionContext,
        kwargs: dict[str, Any],
        gaps: list[ProjectionGap],
    ) -> None:
        names = context.mcp_server_names()
        keep: set[str] = {name for name in context.platform_mcp_servers if name in names}
        if policy.closed:
            for ref in policy.allowed:
                keep.update(
                    server
                    for server in mcp_servers_for(ref, names)
                    if not policy.forbids(ToolRef("mcp", server))
                )
        else:
            keep.update(name for name in names if not policy.forbids(ToolRef("mcp", name)))
        if keep == set(names) and not policy.closed:
            return
        if not context.supports_read_only_mcp_boundary:
            gaps.append(
                ProjectionGap(
                    REFUSE,
                    "allowed_tools:mcp",
                    "this Codex transport cannot isolate MCP servers from the user's Codex "
                    "config, so the persona's MCP limits cannot be enforced",
                )
            )
            return
        kwargs["mcp_servers"] = [dict(s) for s in context.mcp_servers if str(s.get("name")) in keep]
        kwargs["read_only_mcp_only"] = True
        for ref in policy.allowed:
            if ref.kind == "mcp" and not mcp_servers_for(ref, names):
                gaps.append(
                    ProjectionGap(
                        NOTE,
                        f"allowed_tools:{ref.text}",
                        "no such MCP server is configured in this session",
                    )
                )
