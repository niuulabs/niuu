"""Transport-neutral Forge MCP: tool definitions, grants and dispatch.

Hosts plug in two ports — :class:`ForgeClient` (Forge REST) and
:class:`ForgeMcpHost` (the caller's own session) — and expose
:class:`ForgeMcpToolbox` over whichever MCP transport they speak.
"""

from niuu.forge_mcp.models import (
    ForgeApiError,
    ForgeMcpGrant,
    ForgeMcpLimits,
    ForgeMcpNotifyMode,
    SecretRequestError,
    ToolResult,
)
from niuu.forge_mcp.ports import ForgeClient, ForgeMcpHost
from niuu.forge_mcp.tools import (
    FEED_NOTIFY_SPEC,
    TOOL_NAMES,
    TOOL_SPECS,
    ForgeMcpToolbox,
    UnknownToolError,
)

__all__ = [
    "FEED_NOTIFY_SPEC",
    "TOOL_NAMES",
    "TOOL_SPECS",
    "ForgeApiError",
    "ForgeClient",
    "ForgeMcpGrant",
    "ForgeMcpHost",
    "ForgeMcpLimits",
    "ForgeMcpNotifyMode",
    "ForgeMcpToolbox",
    "SecretRequestError",
    "ToolResult",
    "UnknownToolError",
]
