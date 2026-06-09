"""Shared spawn-env construction for the Claude CLI/SDK transports.

Default auth = the host's claude.ai subscription (the OAuth login in
``~/.claude``), exactly like an interactive ``claude`` shell: the
platform-injected ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` are STRIPPED
from the child env so the CLI falls back to the stored login. Rationale: the
deploy env's API key belongs to an org without data retention enabled, which
400s on retention-gated models (``claude-fable-5``) that the same host's
subscription login can use.

Set ``SKULD__CLAUDE_AUTH=api_key`` to restore API-key billing (the key vars are
kept). ``CLAUDECODE`` is always dropped — a nested-session marker that breaks
the spawned CLI.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def claude_spawn_env() -> dict[str, str]:
    """Build the child env for a Claude CLI/SDK spawn (see module docstring)."""
    mode = os.environ.get("SKULD__CLAUDE_AUTH", "subscription").strip().lower()
    if mode == "api_key":
        return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE" and k not in _API_KEY_VARS}
    if not (Path.home() / ".claude" / ".credentials.json").exists():
        logger.warning(
            "SKULD__CLAUDE_AUTH=subscription but ~/.claude/.credentials.json is "
            "missing on this host — run `claude login`, or set "
            "SKULD__CLAUDE_AUTH=api_key to use the platform API key"
        )
    return env
