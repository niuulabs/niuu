"""Which resident channel the running turn belongs to, for its notifications.

A channel that drives a turn (the OpenClaw shim, for each session key) sets
:data:`current_notification_channel` before it starts the turn. The turn's task
inherits the value (``asyncio`` copies the context into tasks it creates), so a
notification the agent sends from that turn names the channel it came from. A
turn nobody set it for (the resident's own loop, the main room) has ``None``.
"""

from __future__ import annotations

from contextvars import ContextVar

current_notification_channel: ContextVar[str | None] = ContextVar(
    "ravn_notification_channel", default=None
)
