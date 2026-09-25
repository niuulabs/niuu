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

With a model gateway (``gateway_url``), the CLI is pointed at it instead of
api.anthropic.com: ``ANTHROPIC_BASE_URL`` + ``ANTHROPIC_AUTH_TOKEN``, and the
platform API key is dropped so it cannot win over the token. The subscription
login stays untouched but unused.

Trace propagation: Claude Code reads ``TRACEPARENT``/``TRACESTATE`` from its
own environment at startup in Agent SDK and non-interactive (``-p``) sessions,
and parents its ``claude_code.interaction`` span under them — documented at
https://code.claude.com/docs/en/agent-sdk/observability. Interactive sessions
ignore inbound ``TRACEPARENT`` (to avoid inheriting ambient CI/container
values), so the env var is harmless-but-inert there. This spawn env always
carries the caller's active W3C trace context (empty when observability is
disabled or no span is active), so whichever mode a transport uses gets it
for free.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from niuu.observability import get_observability

logger = logging.getLogger(__name__)

_API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def claude_spawn_env(*, gateway_url: str = "", gateway_token: str = "") -> dict[str, str]:
    """Build the child env for a Claude CLI/SDK spawn (see module docstring)."""
    if gateway_url.strip():
        env = {
            k: v for k, v in os.environ.items() if k != "CLAUDECODE" and k != "ANTHROPIC_API_KEY"
        }
        env["ANTHROPIC_BASE_URL"] = gateway_url.strip().rstrip("/")
        env["ANTHROPIC_AUTH_TOKEN"] = gateway_token
        logger.info("Claude CLI routed through the model gateway at %s", env["ANTHROPIC_BASE_URL"])
        env.update(get_observability().inject())
        return env

    mode = os.environ.get("SKULD__CLAUDE_AUTH", "subscription").strip().lower()
    if mode == "api_key":
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env.update(get_observability().inject())
        return env

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE" and k not in _API_KEY_VARS}
    # On macOS the CLI stores its OAuth login in the Keychain, so the
    # credentials file only signals a missing login on other platforms.
    if (
        sys.platform != "darwin"
        and not env.get("CLAUDE_CODE_OAUTH_TOKEN")
        and not (Path.home() / ".claude" / ".credentials.json").exists()
    ):
        logger.warning(
            "SKULD__CLAUDE_AUTH=subscription but ~/.claude/.credentials.json is "
            "missing on this host — run `claude login`, or set "
            "SKULD__CLAUDE_AUTH=api_key to use the platform API key"
        )
    env.update(get_observability().inject())
    return env
