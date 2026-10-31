"""Build the environment an agent CLI is spawned with, minus the broker's credentials.

Every transport starts its child from the broker's own environment so the CLI
inherits PATH, HOME, locale and its *own* auth (``ANTHROPIC_API_KEY``,
``OPENAI_API_KEY``, ``XAI_API_KEY``, ``GITHUB_TOKEN``…). The broker's
environment also carries the platform's credentials: the Forge service token
(``SKULD__EXTERNAL_API_TOKEN`` / ``VOLUNDR_EXTERNAL_API_TOKEN``), the scoped
Forge MCP token, and — in local mode, where Forge spawns Skuld with its own
environment — Forge's settings such as ``DATABASE__PASSWORD``. The model can
read its environment (``env`` in a shell tool), so none of those may reach it.

What is removed:

* the explicit broker credentials in :data:`BROKER_CREDENTIAL_VARS`, and
* any *platform-namespaced* variable whose name marks it as a secret. Platform
  settings are either prefixed (``SKULD__``, ``VOLUNDR``, ``NIUU_``) or use the
  pydantic nested delimiter ``__`` (``DATABASE__PASSWORD``); a secret marker is
  ``TOKEN``, ``SECRET``, ``PASSWORD``, ``API_KEY``, ``PRIVATE_KEY`` or
  ``CREDENTIAL``.

Engine auth variables are not platform-namespaced, so they pass through
untouched; whether a Claude API key is *used* stays the business of
``claude_env`` (``SKULD__CLAUDE_AUTH``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

BROKER_CREDENTIAL_VARS: frozenset[str] = frozenset(
    {
        "SKULD__EXTERNAL_API_TOKEN",
        "VOLUNDR_EXTERNAL_API_TOKEN",
        "SKULD__FORGE_MCP__TOKEN",
        "SKULD__TELEGRAM__BOT_TOKEN",
        "SKULD__DSH__API_KEY",
    }
)
_PLATFORM_PREFIXES = ("SKULD__", "VOLUNDR", "NIUU_")
_NESTED_SETTINGS_DELIMITER = "__"
_SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "PRIVATE_KEY", "CREDENTIAL")


def is_broker_credential(name: str) -> bool:
    """True when ``name`` is a platform credential that must never reach the agent."""
    upper = name.upper()
    if upper in BROKER_CREDENTIAL_VARS:
        return True
    namespaced = upper.startswith(_PLATFORM_PREFIXES) or _NESTED_SETTINGS_DELIMITER in upper
    if not namespaced:
        return False
    return any(marker in upper for marker in _SECRET_MARKERS)


def scrub_broker_credentials(env: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``env`` without broker/platform credentials."""
    return {key: value for key, value in env.items() if not is_broker_credential(key)}


def session_process_env() -> dict[str, str]:
    """The broker's environment, scrubbed, as the base for a spawned agent process."""
    return scrub_broker_credentials(os.environ)
