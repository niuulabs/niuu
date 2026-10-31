"""Skuld - Claude Code CLI broker service.

The public names are resolved lazily (PEP 562) so lightweight entry points such
as ``python -m skuld.forge_mcp`` — spawned by every agent CLI for its MCP server —
start without importing the broker, FastAPI and the transport stack.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS = {
    "Broker": "skuld.broker",
    "app": "skuld.broker",
    "CLITransport": "niuu.ports.cli",
    "ChannelRegistry": "skuld.channels",
    "MessageChannel": "skuld.channels",
    "TelegramChannel": "skuld.channels",
    "WebSocketChannel": "skuld.channels",
    "SkuldSettings": "skuld.config",
    "TelegramConfig": "skuld.config",
}
_OPTIONAL_TRANSPORT_EXPORTS = ("SdkWebSocketTransport", "SubprocessTransport")

__all__ = [
    "Broker",
    "CLITransport",
    "ChannelRegistry",
    "MessageChannel",
    "SdkWebSocketTransport",
    "SkuldSettings",
    "SubprocessTransport",
    "TelegramChannel",
    "TelegramConfig",
    "WebSocketChannel",
    "app",
]


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        value = getattr(importlib.import_module(_LAZY_EXPORTS[name]), name)
        globals()[name] = value
        return value
    if name in _OPTIONAL_TRANSPORT_EXPORTS:
        try:
            value = getattr(importlib.import_module("skuld.transport"), name)
        except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency path
            value = None
        globals()[name] = value
        return value
    raise AttributeError(f"module 'skuld' has no attribute {name!r}")
