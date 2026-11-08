"""The neutral tool vocabulary a persona speaks.

A persona names the tools a role may use and may not use. It does not say which
harness runs it, so it cannot name a harness's own tools (``Bash``, ``apply_patch``)
and it can no longer name the tools of the retired Ravn runtime (``terminal``,
``ting_saga``). It names what the role may *do*, in a small closed set every
harness projector (``skuld.personas``) maps onto its own tools or refuses.

Built-in classes:

``file_read``
    Read, list and search files in the workspace.
``file_write``
    Create and edit files in the workspace.
``shell``
    Run arbitrary commands. Includes ``git``, ``git_read`` and every capability
    command below, because a shell can run them.
``git_read``
    Inspect a repository without changing it (status, diff, log, show).
``git``
    Run git, including commands that change the repository. Forbidding it forbids
    only the mutating subset; an explicitly allowed ``git_read`` stays.
``web_fetch`` / ``web_search``
    Fetch a URL / search the web.
``plan``
    Keep a task list. Every harness has one and none can leave a role without it, so
    it is never a restriction.

Niuu capabilities that exist for harness sessions today:

``present_file``
    Show the user a file (the ``present-file`` command).
``tracker``
    Search, read and update tracker issues (the ``tracker_issue`` command, or an
    MCP server named ``tracker``).
``mimir`` / ``mimir:<tool>``
    The Mímir knowledge base, whole or one tool of it (``mimir:search``,
    ``mimir:write``...). Reached through the ``mimir_*`` commands or an MCP server
    named ``mimir`` or ``mimir-<mount>``.

``mcp:<server>`` / ``mcp:<server>:<tool>``
    An MCP server of the session, whole or one of its tools.

Anything else is refused when the persona is loaded, with this set in the message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FILE_READ = "file_read"
FILE_WRITE = "file_write"
SHELL = "shell"
GIT_READ = "git_read"
GIT = "git"
WEB_FETCH = "web_fetch"
WEB_SEARCH = "web_search"
PLAN = "plan"

PRESENT_FILE = "present_file"
TRACKER = "tracker"
MIMIR = "mimir"
MCP_PREFIX = "mcp"

BUILTIN_TOOLS: tuple[str, ...] = (
    FILE_READ,
    FILE_WRITE,
    SHELL,
    GIT_READ,
    GIT,
    WEB_FETCH,
    WEB_SEARCH,
    PLAN,
)
CAPABILITY_TOOLS: tuple[str, ...] = (PRESENT_FILE, TRACKER, MIMIR)

#: Tools of the Mímir toolbox, by their name without the ``mimir_`` prefix. They are the
#: ``mimir_*`` commands a harness session gets (``skuld.transports.tool_shims``) and the
#: tools of Mímir's MCP server (``mimir.mcp``).
MIMIR_TOOLS: tuple[str, ...] = (
    "search",
    "read",
    "read_source",
    "related",
    "list",
    "write",
    "ingest",
    "lint",
    "stats",
    "publish_files",
    "publish_artifact",
)
#: Mímir tools that change the knowledge base.
MIMIR_WRITE_TOOLS: frozenset[str] = frozenset(
    {"write", "ingest", "publish_files", "publish_artifact"}
)

#: Tools the session always has because Skuld provides them (the Forge MCP server a step is
#: closed with), so a persona never lists them and never loses them.
PLATFORM_MCP_SERVERS: tuple[str, ...] = ("forge",)

_MCP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

#: Names of the retired runtime and what to use instead, for the error message. ``None`` means
#: the tool has no counterpart in a harness session and must be removed from the persona.
LEGACY_TOOL_HINTS: dict[str, str | None] = {
    "file": "file_read and/or file_write",
    "read_file": "file_read",
    "glob_search": "file_read",
    "grep_search": "file_read",
    "glob": "file_read",
    "grep": "file_read",
    "edit_file": "file_write",
    "write_file": "file_write",
    "file_edit": "file_write",
    "terminal": "shell",
    "bash": "shell",
    "git_diff": "git_read",
    "git_log": "git_read",
    "git_add": "git",
    "git_commit": "git",
    "git_checkout": "git",
    "web": "web_fetch and/or web_search",
    "todo": "plan",
    "todo_write": "plan",
    "todo_read": "plan",
    "tracker_issue": "tracker",
    "mimir_search": "mimir:search",
    "mimir_read": "mimir:read",
    "mimir_read_source": "mimir:read_source",
    "mimir_write": "mimir:write",
    "mimir_ingest": "mimir:ingest",
    "mimir_list": "mimir:list",
    "mimir_related": "mimir:related",
    "mimir_query": "mimir:search",
    "mimir_publish_files": "mimir:publish_files",
    "mimir_publish_artifact": "mimir:publish_artifact",
    "ravn": None,
    "cascade": None,
    "volundr": None,
    "volundr_session": None,
    "session_join": None,
    "a2a_task": None,
    "capability_list": None,
    "ting_saga": None,
    "ting_workflow": None,
    "ting_research": None,
    "ting_plan": None,
    "ting_spec": None,
}


class PersonaToolError(ValueError):
    """A persona names a tool that is not in the neutral vocabulary."""


@dataclass(frozen=True)
class ToolRef:
    """One parsed vocabulary entry.

    ``kind`` is ``builtin`` (``name`` is the class), ``capability`` (``name`` is
    ``present_file``/``tracker``/``mimir``, ``tool`` the Mímir tool or ``""`` for the whole
    toolbox) or ``mcp`` (``name`` is the server, ``tool`` the tool or ``""``).
    """

    kind: str
    name: str
    tool: str = ""

    @property
    def text(self) -> str:
        if self.kind == "builtin":
            return self.name
        if self.kind == "capability":
            return f"{self.name}:{self.tool}" if self.tool else self.name
        return f"{MCP_PREFIX}:{self.name}:{self.tool}" if self.tool else f"{MCP_PREFIX}:{self.name}"


def valid_tool_names_text() -> str:
    """The vocabulary as one line, for error messages."""
    mimir = f"{MIMIR}:<{'|'.join(MIMIR_TOOLS)}>"
    return (
        f"{', '.join(BUILTIN_TOOLS)}, {', '.join(CAPABILITY_TOOLS)}, {mimir}, "
        f"{MCP_PREFIX}:<server>, {MCP_PREFIX}:<server>:<tool>"
    )


def parse_tool(name: str) -> ToolRef:
    """Parse one tool name or raise :class:`PersonaToolError` naming the valid set."""
    text = name.strip() if isinstance(name, str) else ""
    if text in BUILTIN_TOOLS:
        return ToolRef("builtin", text)
    if text in (PRESENT_FILE, TRACKER, MIMIR):
        return ToolRef("capability", text)
    head, _, rest = text.partition(":")
    if head == MIMIR and rest in MIMIR_TOOLS:
        return ToolRef("capability", MIMIR, rest)
    if head == MCP_PREFIX and rest:
        server, _, tool = rest.partition(":")
        if _MCP_NAME.match(server) and (not tool or _MCP_NAME.match(tool)):
            return ToolRef("mcp", server, tool)
    raise PersonaToolError(_unknown_message(name))


def _unknown_message(name: object) -> str:
    shown = repr(name)
    hint = ""
    if isinstance(name, str) and name in LEGACY_TOOL_HINTS:
        replacement = LEGACY_TOOL_HINTS[name]
        hint = (
            f" {name!r} is a tool of the retired Ravn runtime; use {replacement}."
            if replacement
            else f" {name!r} is a tool of the retired Ravn runtime that has no counterpart in a "
            "harness session; remove it."
        )
    return f"Unknown persona tool {shown}.{hint} Valid tools: {valid_tool_names_text()}"


def validate_tool_names(field: str, names: object) -> list[ToolRef]:
    """Parse every name of a persona tool list; the error names the field and the entry."""
    if not isinstance(names, list):
        raise PersonaToolError(f"Persona {field} must be a list of tool names")
    refs: list[ToolRef] = []
    for name in names:
        try:
            refs.append(parse_tool(name))
        except PersonaToolError as exc:
            raise PersonaToolError(f"Persona {field}: {exc}") from None
    return refs
