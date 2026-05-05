"""CLI transport abstraction for communicating with AI coding CLIs."""

from niuu.adapters.cli.runtime import (
    drain_process_stream as _drain_stream,
)
from niuu.adapters.cli.runtime import (
    filter_cli_event as _filter_event,
)
from niuu.adapters.cli.runtime import (
    stop_subprocess as _stop_process,
)
from niuu.ports.cli import CLITransport, EventCallback, TransportCapabilities

# Re-export concrete transports so `from skuld.transports import X` works
from skuld.transports.codex import (  # noqa: E402
    _CODEX_TOOL_MAP,
    CodexSubprocessTransport,
    _map_codex_tool,
)
from skuld.transports.codex_ws import CodexWebSocketTransport  # noqa: E402
from skuld.transports.opencode import OpenCodeHttpTransport  # noqa: E402
from skuld.transports.sdk_websocket import SdkWebSocketTransport  # noqa: E402
from skuld.transports.subprocess import SubprocessTransport  # noqa: E402

__all__ = [
    "CLITransport",
    "CodexSubprocessTransport",
    "CodexWebSocketTransport",
    "EventCallback",
    "OpenCodeHttpTransport",
    "SdkWebSocketTransport",
    "SubprocessTransport",
    "TransportCapabilities",
    "_CODEX_TOOL_MAP",
    "_drain_stream",
    "_filter_event",
    "_map_codex_tool",
    "_stop_process",
]
