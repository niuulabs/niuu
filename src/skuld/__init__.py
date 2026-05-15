"""Skuld - Claude Code CLI broker service."""

from niuu.ports.cli import CLITransport
from skuld.broker import Broker, app
from skuld.channels import (
    ChannelRegistry,
    MessageChannel,
    TelegramChannel,
    WebSocketChannel,
)
from skuld.config import SkuldSettings, TelegramConfig

try:
    from skuld.transport import SdkWebSocketTransport, SubprocessTransport
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency path
    SdkWebSocketTransport = None  # type: ignore[assignment]
    SubprocessTransport = None  # type: ignore[assignment]

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
