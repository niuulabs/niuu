"""MCP server lifecycle and Mimir result projection for Ravn runtimes."""

import json
import logging
from typing import Any

from ravn.config import Settings
from ravn.domain.models import ToolResult
from ravn.ports.executor import ExecutionAgentPort

logger = logging.getLogger(__name__)


async def _start_mcp(
    settings: Settings,
    agent: ExecutionAgentPort,
) -> Any | None:
    """Start MCP servers and register discovered tools into the agent.

    Returns the MCPManager instance (for shutdown), or None if no MCP
    servers are configured.
    """
    if not settings.mcp_servers:
        return None

    from ravn.adapters.mcp.auth import MCPAuthSession
    from ravn.adapters.mcp.manager import MCPManager
    from ravn.adapters.tools.mcp import MCPAuthTool

    # Build token store
    ts_cfg = settings.mcp_token_store
    if ts_cfg.backend == "openbao":
        from ravn.adapters.mcp.auth import OpenBaoTokenStore

        store = OpenBaoTokenStore(
            url=ts_cfg.openbao_url,
            token_env=ts_cfg.openbao_token_env,
            mount=ts_cfg.openbao_mount,
            path_prefix=ts_cfg.openbao_path_prefix,
        )
    else:
        from ravn.adapters.mcp.auth import LocalEncryptedTokenStore

        store = LocalEncryptedTokenStore(path=ts_cfg.local_path)

    auth_session = MCPAuthSession(store)
    builtin_names = set(agent._tools.keys())
    manager = MCPManager(settings.mcp_servers, builtin_tool_names=builtin_names)

    try:
        mcp_tools = await manager.start()
    except Exception as exc:
        logger.warning("MCP startup failed: %s — continuing without MCP tools", exc)
        return None

    # Register discovered tools into the agent
    for tool in mcp_tools:
        agent._tools[tool.name] = tool

    # Add the auth tool so the model can trigger auth flows
    server_configs = {s.name: s for s in settings.mcp_servers if s.enabled}
    auth_tool = MCPAuthTool(auth_session, server_configs, manager=manager)
    agent._tools[auth_tool.name] = auth_tool

    logger.info(
        "MCP started: %d server(s), %d tool(s) discovered",
        len(settings.mcp_servers),
        len(mcp_tools),
    )
    return manager


async def _start_mcp_shared(
    settings: Settings,
    *,
    tool_result_hook: Any | None = None,
) -> tuple[Any | None, list[Any]]:
    """Start MCP servers and return (manager, tools) for gateway use.

    Unlike :func:`_start_mcp`, this does not inject tools into an agent
    because the gateway creates agents per-session.  The returned tool
    list should be appended to each session's tool list.
    """
    if not settings.mcp_servers:
        return None, []

    from ravn.adapters.mcp.auth import MCPAuthSession
    from ravn.adapters.mcp.manager import MCPManager
    from ravn.adapters.tools.mcp import MCPAuthTool

    ts_cfg = settings.mcp_token_store
    if ts_cfg.backend == "openbao":
        from ravn.adapters.mcp.auth import OpenBaoTokenStore

        store = OpenBaoTokenStore(
            url=ts_cfg.openbao_url,
            token_env=ts_cfg.openbao_token_env,
            mount=ts_cfg.openbao_mount,
            path_prefix=ts_cfg.openbao_path_prefix,
        )
    else:
        from ravn.adapters.mcp.auth import LocalEncryptedTokenStore

        store = LocalEncryptedTokenStore(path=ts_cfg.local_path)

    auth_session = MCPAuthSession(store)
    manager = MCPManager(settings.mcp_servers, tool_result_hook=tool_result_hook)

    try:
        mcp_tools: list[Any] = await manager.start()
    except Exception as exc:
        logger.warning("MCP startup failed: %s — continuing without MCP tools", exc)
        return None, []

    server_configs = {s.name: s for s in settings.mcp_servers if s.enabled}
    auth_tool = MCPAuthTool(auth_session, server_configs, manager=manager)
    mcp_tools.append(auth_tool)

    logger.info(
        "MCP started: %d server(s), %d tool(s) discovered",
        len(settings.mcp_servers),
        len(mcp_tools) - 1,  # exclude auth tool from count
    )
    return manager, mcp_tools


def _mimir_mount_name_from_mcp_server_name(server_name: str) -> str | None:
    if not server_name.startswith("mimir-"):
        return None
    mount_name = server_name.removeprefix("mimir-").strip()
    return mount_name or None


def _mimir_ingest_event_fields_from_mcp_result(
    *,
    server_name: str,
    arguments: dict[str, Any],
    result: ToolResult,
) -> dict[str, Any] | None:
    if result.is_error:
        return None

    try:
        parsed = json.loads(result.content)
    except Exception:
        logger.debug("Failed to parse mimir_ingest MCP result from %s", server_name, exc_info=True)
        return None

    if not isinstance(parsed, dict):
        return None

    source_id = str(parsed.get("source_id") or "").strip()
    if not source_id:
        return None

    page_paths_raw = parsed.get("pages_updated")
    page_paths = page_paths_raw if isinstance(page_paths_raw, list) else []
    mount_name = _mimir_mount_name_from_mcp_server_name(server_name)
    source_title = str(parsed.get("title") or arguments.get("title") or source_id).strip()
    source_type = str(
        parsed.get("source_type") or arguments.get("source_type") or "document"
    ).strip()

    fields: dict[str, Any] = {
        "source_id": source_id,
        "source_title": source_title or source_id,
        "source_type": source_type or "document",
        "page_paths": [str(path) for path in page_paths if str(path).strip()],
        "mcp_server_name": server_name,
    }
    if mount_name:
        fields["mount_name"] = mount_name
        fields["mount_names"] = [mount_name]
    return fields


def _mimir_write_event_fields_from_mcp_result(
    *,
    server_name: str,
    arguments: dict[str, Any],
    result: ToolResult,
) -> dict[str, Any] | None:
    if result.is_error:
        return None

    page_path = str(arguments.get("path") or "").strip()
    if not page_path:
        content = str(result.content or "").strip()
        prefix = "Page written: "
        if content.startswith(prefix):
            page_path = content[len(prefix) :].split(" (routed to:", 1)[0].strip()
    if not page_path:
        return None

    explicit_mimir = str(arguments.get("mimir") or "").strip()
    mount_name = explicit_mimir or _mimir_mount_name_from_mcp_server_name(server_name)

    fields: dict[str, Any] = {
        "page_path": page_path,
        "mcp_server_name": server_name,
    }
    if mount_name:
        fields["mount_name"] = mount_name
        fields["mount_names"] = [mount_name]
    return fields


async def _shutdown_mcp(manager: Any | None) -> None:
    """Gracefully shut down MCP servers."""
    if manager is None:
        return
    try:
        await manager.shutdown()
    except Exception as exc:
        logger.warning("MCP shutdown error: %s", exc)
