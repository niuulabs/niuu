"""MCP servers a session asks for by name, from the operator's catalogue (``mcp_servers``)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from credentials.models import MCPServerConfig
from credentials.ports import MCPServerProvider

#: Where a session's requested server names live in its workload config, so that a restart
#: gives the session the same servers.
MCP_SERVERS_KEY = "mcp_servers"


class ProgramNotInCatalogueError(ValueError):
    """A launch spec brings its own program; only the operator's catalogue may name one."""


def refuse_own_programs(servers: Iterable[dict[str, Any]]) -> None:
    """Refuse servers that name a program to install: what a Forge installs, its operator decides.

    Raises:
        ProgramNotInCatalogueError: for the first server that does.
    """
    for server in servers:
        if "program" in server:
            raise ProgramNotInCatalogueError(
                f"MCP server {server.get('name')!r} brings its own program. Only this Forge's "
                "mcp_servers catalogue may name one; give the server's catalogue name alone"
            )


class UnknownMCPServerError(ValueError):
    """A session asks for an MCP server the catalogue does not have."""


def requested_mcp_servers(workload_config: dict[str, Any]) -> list[str]:
    """The catalogue names a session asks for."""
    raw = workload_config.get(MCP_SERVERS_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(name, str) and name for name in raw):
        raise ValueError(f"workload_config.{MCP_SERVERS_KEY} must be a list of MCP server names")
    return list(dict.fromkeys(raw))


def catalogue_entries(
    names: Iterable[str], provider: MCPServerProvider | None
) -> list[dict[str, Any]]:
    """The session entries of *names*; an unknown name raises, naming what there is."""
    return [_entry(name, provider) for name in names]


def _entry(name: str, provider: MCPServerProvider | None) -> dict[str, Any]:
    server = provider.get(name) if provider is not None else None
    if server is not None:
        return session_entry(server)
    known = ", ".join(sorted(s.name for s in provider.list())) if provider is not None else ""
    raise UnknownMCPServerError(
        f"MCP server {name!r} is not in this Forge's mcp_servers catalogue "
        f"(it has: {known or 'none'})"
    )


def session_entry(server: MCPServerConfig) -> dict[str, Any]:
    """A catalogue server as Skuld takes it. Asked for by name, so the session needs it."""
    entry: dict[str, Any] = {"name": server.name, "type": server.type, "required": True}
    for key in ("command", "url", "description", "program", "env"):
        value = getattr(server, key)
        if value:
            entry[key] = value
    if server.args:
        entry["args"] = list(server.args)
    for key in ("default_tools_approval_mode", "startup_timeout_sec", "tool_timeout_sec"):
        value = getattr(server, key)
        if value is not None:
            entry[key] = value
    return entry
