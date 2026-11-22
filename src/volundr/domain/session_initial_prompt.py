"""A session's first message, kept on its workload config until it is used.

The prompt is given once, when the session is created, and the launch that carries it can
end before the agent ever takes it: the runtime may fail to come up, or the session may be
stopped first. Every launch therefore records it (``INITIAL_PROMPT_KEY``), so a start that
brings no prompt of its own carries the session's. Skuld delivers it at most once, under a
durable delivery claim (``skuld.initial_prompt``); a session that has run a turn is not
given it again at all.

A hosted member's launch arguments are kept as ``HostedLaunch`` instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Where the prompt lives in ``Session.workload_config``.
INITIAL_PROMPT_KEY = "_initial_prompt"


def initial_prompt_of(workload_config: Mapping[str, Any] | None) -> str:
    """The session's stored first message ("" for a session created without one)."""
    if not workload_config:
        return ""
    return str(workload_config.get(INITIAL_PROMPT_KEY) or "")


def with_initial_prompt(workload_config: Mapping[str, Any], prompt: str) -> dict[str, Any]:
    """``workload_config`` recording ``prompt`` (and nothing at all for an empty one)."""
    config = without_initial_prompt(workload_config)
    if prompt:
        config[INITIAL_PROMPT_KEY] = prompt
    return config


def without_initial_prompt(workload_config: Mapping[str, Any]) -> dict[str, Any]:
    """The workload config without the record (what contributors and callers see)."""
    return {key: value for key, value in workload_config.items() if key != INITIAL_PROMPT_KEY}
