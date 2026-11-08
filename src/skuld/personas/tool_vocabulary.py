"""How the neutral persona tool vocabulary is realised by the tools sessions really have.

``personas.domain.tool_vocabulary`` says what a persona may name. This module says what each
name is on a harness session today:

* Claude's built-in tools (``Read``, ``Bash``...);
* the PATH commands Skuld installs (``skuld.transports.tool_shims``): ``present-file``,
  ``tracker_issue`` and the ``mimir_*`` commands, all run through the shell;
* the session's MCP servers: ``mimir`` / ``mimir-<mount>`` for Mímir, ``tracker`` for the
  tracker, and any server a persona names with ``mcp:<server>``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from personas.domain.tool_vocabulary import (
    MIMIR,
    MIMIR_TOOLS,
    PRESENT_FILE,
    TRACKER,
    ToolRef,
)

CLAUDE_FILE_READ: tuple[str, ...] = ("Read", "Glob", "Grep")
CLAUDE_FILE_WRITE: tuple[str, ...] = ("Write", "Edit", "NotebookEdit")
CLAUDE_SHELL = "Bash"
CLAUDE_WEB_FETCH = "WebFetch"
CLAUDE_WEB_SEARCH = "WebSearch"
CLAUDE_PLAN: tuple[str, ...] = ("TodoWrite", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList")

#: Commands that inspect a repository without changing it, as the words a shell prefix matches.
GIT_READ_COMMANDS: tuple[str, ...] = ("git status", "git diff", "git log", "git show")

#: ``mimir:<tool>`` is the command ``mimir_<tool>`` on PATH and the MCP tool of that name. The
#: MCP server has ``lint`` and ``stats`` too; no PATH command does.
MIMIR_COMMAND_TOOLS: tuple[str, ...] = tuple(t for t in MIMIR_TOOLS if t not in {"lint", "stats"})

PRESENT_FILE_COMMAND = "present-file"
TRACKER_COMMAND = "tracker_issue"


def mimir_command(tool: str) -> str:
    return f"mimir_{tool}"


def shell_commands(ref: ToolRef) -> tuple[str, ...]:
    """The PATH commands that realise a command-backed ref, or none."""
    if ref.kind == "builtin":
        return GIT_READ_COMMANDS if ref.name == "git_read" else ()
    if ref.kind != "capability":
        return ()
    if ref.name == PRESENT_FILE:
        return (PRESENT_FILE_COMMAND,)
    if ref.name == TRACKER:
        return (TRACKER_COMMAND,)
    if ref.tool:
        return (mimir_command(ref.tool),) if ref.tool in MIMIR_COMMAND_TOOLS else ()
    return tuple(mimir_command(tool) for tool in MIMIR_COMMAND_TOOLS)


def capability_servers(capability: str, server_names: Iterable[str]) -> list[str]:
    """MCP servers of a Niuu capability: ``<capability>`` or ``<capability>-<anything>``."""
    return [
        name for name in server_names if name == capability or name.startswith(f"{capability}-")
    ]


def mcp_servers_for(ref: ToolRef, server_names: Iterable[str]) -> list[str]:
    """Session MCP servers that realise a ref (``mcp:`` entries and Niuu capabilities)."""
    names = list(server_names)
    if ref.kind == "mcp":
        return [ref.name] if ref.name in names else []
    if ref.kind == "capability" and ref.name in (MIMIR, TRACKER):
        return capability_servers(ref.name, names)
    return []


def mcp_tool_name(ref: ToolRef) -> str:
    """The tool name inside a realising MCP server, or empty for the whole server."""
    if ref.kind == "mcp":
        return ref.tool
    if ref.kind == "capability" and ref.name == MIMIR and ref.tool:
        return mimir_command(ref.tool)
    return ""


def server_name_list(servers: Iterable[Mapping[str, Any]]) -> list[str]:
    return [str(server.get("name") or "") for server in servers if server.get("name")]
