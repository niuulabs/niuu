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

``SKULD__CLAUDE_OAUTH_TOKEN_FILE`` optionally supplies an owner-only 0400 file
for direct subscription inference. Conflicting auth settings or an invalid
file refuse the launch. Its token is read afresh into the Claude child env,
never the broker's environment or another engine's environment.

With a model gateway (``gateway_url``), the CLI is pointed at it instead of
api.anthropic.com: ``ANTHROPIC_BASE_URL`` + ``ANTHROPIC_AUTH_TOKEN``, and the
platform API key is dropped so it cannot win over the token. The subscription
login stays untouched but unused.

A blank ``gateway_token`` alongside a set ``gateway_url`` is refused (raises
``ValueError``) rather than sent as ``ANTHROPIC_AUTH_TOKEN=""``: an empty
override reads as "not logged in" in a container, or on a host with a stored
subscription login, as no override at all — the CLI would then fall back to
sending the user's real subscription OAuth token to the gateway instead of
the intended credential. ``volundr.adapters.outbound.contributors.
model_gateway.ModelGatewayContributor`` always supplies a non-blank token
(``OPEN_GATEWAY_TOKEN`` under 'none'/'envoy') whenever it sets
``gateway_url``, so this should only ever fire for a caller that bypassed
that contributor.

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
import stat
import sys
from pathlib import Path

from niuu.observability import get_observability
from skuld.transports.session_env import CLAUDE_OAUTH_ENV_VARS, session_process_env

logger = logging.getLogger(__name__)

_API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
CLAUDE_OAUTH_TOKEN_FILE_ENV = "SKULD__CLAUDE_OAUTH_TOKEN_FILE"
_MAX_OAUTH_TOKEN_BYTES = 4096


def _read_oauth_token_file(filename: str) -> str:
    """Read the operator's private token without exposing content or paths in errors."""
    try:
        if not Path(filename).is_absolute():
            raise ValueError
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o400
                or info.st_nlink != 1
            ):
                raise ValueError
            raw = source.read(_MAX_OAUTH_TOKEN_BYTES + 2)
        token = raw[:-1] if raw.endswith(b"\n") else raw
        if not 1 <= len(token) <= _MAX_OAUTH_TOKEN_BYTES or any(
            byte < 33 or byte > 126 for byte in token
        ):
            raise ValueError
        return token.decode("ascii")
    except (OSError, ValueError):
        raise ValueError("Configured Claude OAuth token file is invalid or unreadable") from None


def claude_spawn_env(*, gateway_url: str = "", gateway_token: str = "") -> dict[str, str]:
    """Build the child env for a Claude CLI/SDK spawn (see module docstring)."""
    mode = os.environ.get("SKULD__CLAUDE_AUTH", "subscription").strip().lower()
    token_file = os.environ.get(CLAUDE_OAUTH_TOKEN_FILE_ENV)
    if token_file is not None and (
        mode != "subscription"
        or gateway_url.strip()
        or any(
            os.environ.get(name)
            for name in (*_API_KEY_VARS, "ANTHROPIC_BASE_URL", *CLAUDE_OAUTH_ENV_VARS)
        )
    ):
        raise ValueError("Claude OAuth token file requires unambiguous direct subscription auth")
    if gateway_url.strip():
        if not gateway_token.strip():
            raise ValueError(
                f"Model gateway URL {gateway_url.strip()!r} is set but gateway_token is "
                "blank. Sending ANTHROPIC_AUTH_TOKEN='' would read as 'not logged in' in "
                "a container, or fall back to the host's real subscription OAuth token "
                "being sent to the gateway instead — never a silent, unauthenticated "
                "session. Configure model_gateway.token (see skuld.config."
                "ModelGatewayConfig), or fix the session contributor that should have "
                "supplied one (volundr.adapters.outbound.contributors.model_gateway)."
            )
        # never hand the broker's credentials to the model
        env = {
            k: v
            for k, v in session_process_env().items()
            if k != "CLAUDECODE" and k != "ANTHROPIC_API_KEY"
        }
        env["ANTHROPIC_BASE_URL"] = gateway_url.strip().rstrip("/")
        env["ANTHROPIC_AUTH_TOKEN"] = gateway_token
        logger.info("Claude CLI routed through the model gateway at %s", env["ANTHROPIC_BASE_URL"])
        env.update(get_observability().inject())
        return env

    # Existing direct subscription env auth is a Claude-only opt-in. File mode
    # injects only into this returned child dictionary, never the broker env.
    base = session_process_env(claude_oauth=mode != "api_key" and token_file is None)
    if mode == "api_key":
        env = {k: v for k, v in base.items() if k != "CLAUDECODE"}
        env.update(get_observability().inject())
        return env

    env = {k: v for k, v in base.items() if k != "CLAUDECODE" and k not in _API_KEY_VARS}
    if token_file is not None:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = _read_oauth_token_file(token_file)
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
