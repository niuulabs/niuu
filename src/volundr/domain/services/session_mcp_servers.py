"""MCP servers a session asks for by name, from the operator's catalogue (``mcp_servers``)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from credentials.models import MCPServerConfig
from credentials.ports import MCPServerProvider
from niuu.domain.oauth_credentials import mcp_token_path
from volundr.domain.session_runtimes import HOST_SELECTOR_KEY

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


class MCPServerHostError(ValueError):
    """A session asks for a server whose host tags its placement cannot satisfy."""


def with_server_host_tags(
    workload_config: dict[str, Any], servers: Iterable[MCPServerConfig]
) -> dict[str, Any]:
    """*workload_config* whose host selector also needs every host tag of *servers*.

    The tags join the selector the launch brought (``all`` with its own tags); an ``any``
    selector of several tags cannot take them and is refused, as is a malformed one.
    """
    needed = sorted({tag for server in servers for tag in server.host_tags})
    if not needed:
        return workload_config
    users = ", ".join(sorted(server.name for server in servers if server.host_tags))
    raw = workload_config.get(HOST_SELECTOR_KEY)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise MCPServerHostError(f'{HOST_SELECTOR_KEY} must be {{"tags": [...], "match": ...}}')
    tags = list(raw.get("tags") or [])
    if raw.get("match", "all") == "any" and len(tags) > 1:
        raise MCPServerHostError(
            f"MCP servers {users} run only on hosts tagged {needed}, which a host selector "
            f"that matches any of {tags} cannot add; target the hosts with match all"
        )
    return {
        **workload_config,
        HOST_SELECTOR_KEY: {"tags": sorted({*tags, *needed}), "match": "all"},
    }


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
    return [session_entry(server) for server in catalogue_servers(names, provider)]


def catalogue_servers(
    names: Iterable[str], provider: MCPServerProvider | None
) -> list[MCPServerConfig]:
    """The catalogue's servers called *names*; an unknown name raises, naming what there is."""
    return [_server(name, provider) for name in names]


def token_file(server: MCPServerConfig) -> str:
    """Where a session finds the token it sends to *server* (``server.credential``)."""
    return mcp_token_path(f"catalogue:{server.name}")


def _server(name: str, provider: MCPServerProvider | None) -> MCPServerConfig:
    server = provider.get(name) if provider is not None else None
    if server is not None:
        return server
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
    if server.credential:
        entry["credential_file"] = token_file(server)
    if server.args:
        entry["args"] = list(server.args)
    for key in ("default_tools_approval_mode", "startup_timeout_sec", "tool_timeout_sec"):
        value = getattr(server, key)
        if value is not None:
            entry[key] = value
    return entry
