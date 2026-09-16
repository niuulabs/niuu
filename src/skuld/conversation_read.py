"""Read-only conversation references shared by HTTP and WebSocket snapshots."""

import asyncio
from functools import wraps

from niuu.domain.conversation_timeline import project_timeline


def history_write(method):
    """Observe in-flight mutations without serializing or delaying agent/input work.

    CLI events currently broadcast before reducing, with I/O awaits between.
    Snapshot readers must not claim that partially applied event is a boundary.
    Only readers wait; writers retain their original concurrency/delivery flow.
    """

    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        self._history_writes_in_flight = getattr(self, "_history_writes_in_flight", 0) + 1
        if not hasattr(self, "_history_quiet"):
            self._history_quiet = asyncio.Event()
        self._history_quiet.clear()
        try:
            return await method(self, *args, **kwargs)
        finally:
            self._history_writes_in_flight -= 1
            if not self._history_writes_in_flight:
                self._history_quiet.set()

    return wrapped


async def wait_history_quiet(broker: object) -> None:
    """Return at a synchronous snapshot boundary, or fail for bounded recovery."""
    async with asyncio.timeout(broker._settings.history_read_timeout_seconds):
        while getattr(broker, "_history_writes_in_flight", 0):
            await broker._history_quiet.wait()


def conversation_rows(broker: object, *, omit_empty_participants: bool = False) -> list[dict]:
    # Do not asdict/deep-copy every historical tool result to serve fifteen rows.
    turns = [vars(turn).copy() for turn in broker._conversation_turns]
    for turn in turns if omit_empty_participants else []:
        for key in ("participant_id", "participant_meta", "thread_id"):
            if turn.get(key) is None:
                turn.pop(key, None)
    active = broker._serialize_in_progress_turn()
    if active is not None:
        turns.append(active)
    return project_timeline(turns, broker.session_id)
