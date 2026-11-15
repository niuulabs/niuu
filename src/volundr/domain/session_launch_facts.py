"""Facts a session's current launch established, recorded on its workload config.

Some things are decided per launch by the contributor pipeline and must be known about
the session afterwards: whether its broker runs the voice channel, for one. Each launch
replaces the whole record (``LAUNCH_FACTS_KEY``) with what that launch's contributors
reported, so a fact never outlives the launch that made it: a session started while
voice was on and restarted after it was switched off no longer claims a voice channel.
A caller cannot set the record: the launch overwrites whatever a request carried.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Where the record lives in ``Session.workload_config``.
LAUNCH_FACTS_KEY = "_launch_facts"

#: The broker of this launch runs the session voice channel (``skuld.voice``).
VOICE_CHANNEL_FACT = "voice_channel"


def launch_facts_of(workload_config: Mapping[str, Any] | None) -> dict[str, Any]:
    """The current launch's facts (empty for a session never launched with any)."""
    if not workload_config:
        return {}
    facts = workload_config.get(LAUNCH_FACTS_KEY)
    return dict(facts) if isinstance(facts, Mapping) else {}


def without_launch_facts(workload_config: Mapping[str, Any]) -> dict[str, Any]:
    """The workload config without the record (what contributors and callers see)."""
    return {key: value for key, value in workload_config.items() if key != LAUNCH_FACTS_KEY}


def voice_channel_launched(workload_config: Mapping[str, Any] | None) -> bool:
    """Whether the session's current launch runs the voice channel."""
    return launch_facts_of(workload_config).get(VOICE_CHANNEL_FACT) is True
