"""External session provider adapters.

Each adapter implements the ExternalSessionProvider port for one CLI
harness's on-disk session store (Claude Code, Codex, ...).
"""

from volundr.adapters.outbound.external_sessions.claude_code import (
    ClaudeCodeSessionProvider,
)
from volundr.adapters.outbound.external_sessions.codex import CodexSessionProvider

__all__ = [
    "ClaudeCodeSessionProvider",
    "CodexSessionProvider",
]
