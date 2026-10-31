"""Transport-neutral Forge MCP: tool definitions, grants and dispatch.

Hosts plug in two ports — :class:`ForgeClient` (Forge REST) and
:class:`ForgeMcpHost` (the caller's own session) — and expose
:class:`ForgeMcpToolbox` over whichever MCP transport they speak.
"""

from niuu.forge_mcp.models import (
    FlockError,
    ForgeApiError,
    ForgeMcpGrant,
    ForgeMcpLimits,
    ForgeMcpNotifyMode,
    ToolResult,
)
from niuu.forge_mcp.ports import ForgeClient, ForgeMcpFlock, ForgeMcpHost
from niuu.forge_mcp.tools import (
    FEED_NOTIFY_SPEC,
    FLOCK_TOOL_SPECS,
    TOOL_NAMES,
    TOOL_SPECS,
    ForgeMcpToolbox,
    UnknownToolError,
)

__all__ = [
    "FEED_NOTIFY_SPEC",
    "FLOCK_TOOL_SPECS",
    "TOOL_NAMES",
    "TOOL_SPECS",
    "FlockError",
    "ForgeApiError",
    "ForgeClient",
    "ForgeMcpFlock",
    "ForgeMcpGrant",
    "ForgeMcpHost",
    "ForgeMcpLimits",
    "ForgeMcpNotifyMode",
    "ForgeMcpToolbox",
    "ToolResult",
    "UnknownToolError",
]
