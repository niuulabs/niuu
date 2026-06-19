"""Replay-as-live: re-emit recorded session_event_log frames over a WebSocket.

Reads recorded frames (from the durable :class:`SessionEventLogRepository`, or
from a checked-in fixture file for offline/CI use) and re-emits the raw payloads
in ``seq`` order, *paced* by the inter-frame ``ts`` deltas, so the existing
live-session WebSocket client renders them as if they were arriving live.
"""
