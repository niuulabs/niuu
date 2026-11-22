"""Durable transcript reads: shared while they run, shallow results remembered.

A session without a reachable pod (stopped, its pod down, or its broker not back yet after
a Forge restart) is served from its durable event log. A client polls the conversation
every few seconds, often several requests at once, so each read is shared:

* **Shallow** (tool_result-elided) reads come from the incremental fold
  (``ForgeService.get_shallow_transcript``), which reads the log's settled turns page by
  page once and afterwards only the frames since the last turn ended. One runs per session
  at a time, and its result is kept per session, keyed on the event log's ``MAX(seq)``: at
  an unchanged seq a read is a dictionary hit. Only the elided form is kept, only for the
  most recently used sessions. A session without an event log (seq 0: archive or workspace
  transcript) is never cached, as nothing says when it changed.
* **Full** reads (tool-result lookups, the transcript download, ``detail=full``) rebuild the
  transcript, hundreds of MB of Python objects on a big session (43k events, 136 MB of log
  rows on Valhalla). They are not cached, but requests that arrive while one runs share the
  next one instead of rebuilding in turn. That one starts after they asked, so it holds
  everything durable by then.
"""

from __future__ import annotations

import asyncio
import weakref
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID

from skuld.conversation_shallow import elide_turn, elide_turns

#: Sessions whose shallow transcript is kept. Elided transcripts are small, but a few
#: thousand turns of text still add up; this bounds the worst case.
DEFAULT_MAX_SESSIONS = 16


class _TranscriptSource(Protocol):
    async def get_transcript(self, session_id: UUID) -> dict:
        """The full durable transcript (``ForgeService.get_transcript``)."""

    async def get_shallow_transcript(
        self, session_id: UUID, *, elide_turn: Callable[[Any], Any]
    ) -> dict:
        """The elided durable transcript (``ForgeService.get_shallow_transcript``)."""

    async def durable_latest_seq(self, session_id: UUID) -> int:
        """The event log's ``MAX(seq)``, 0 without one."""


class DurableTranscripts:
    """Shared durable reads, with the shallow result cached per (session, seq)."""

    def __init__(self, max_sessions: int = DEFAULT_MAX_SESSIONS) -> None:
        self._max_sessions = max_sessions
        self._shallow: OrderedDict[str, tuple[int, dict[str, Any]]] = OrderedDict()
        # One lock per session, alive only while someone holds or waits on it.
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        # The full rebuild of a session that has not started yet, shared by its requests.
        self._queued: dict[str, asyncio.Task[dict]] = {}

    def _lock(self, sid: str) -> asyncio.Lock:
        lock = self._locks.get(sid)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[sid] = lock
        return lock

    async def full(self, source: _TranscriptSource, session_id: UUID) -> dict:
        """The full durable transcript, shared with every request waiting for the same one."""
        sid = str(session_id)
        task = self._queued.get(sid)
        if task is None:
            task = asyncio.create_task(self._rebuild(source, session_id))
            self._queued[sid] = task
        # A request that goes away must not cancel the rebuild others wait for.
        return await asyncio.shield(task)

    async def _rebuild(self, source: _TranscriptSource, session_id: UUID) -> dict:
        sid = str(session_id)
        async with self._lock(sid):
            # From here a new request may hold more than this rebuild reads: it queues its own.
            self._queued.pop(sid, None)
            return await source.get_transcript(session_id)

    async def shallow(self, source: _TranscriptSource, session_id: UUID) -> dict:
        """The elided durable transcript, refreshed only when the event log grew."""
        sid = str(session_id)
        async with self._lock(sid):
            seq = await source.durable_latest_seq(session_id)
            hit = self._shallow.get(sid)
            if hit is not None and seq > 0 and hit[0] == seq:
                self._shallow.move_to_end(sid)
                return hit[1]
            payload = await source.get_shallow_transcript(session_id, elide_turn=elide_turn)
            elided = _elided(payload)
            if seq > 0:
                self._shallow[sid] = (seq, elided)
                self._shallow.move_to_end(sid)
                while len(self._shallow) > self._max_sessions:
                    self._shallow.popitem(last=False)
            else:
                self._shallow.pop(sid, None)
            return elided

    def clear(self) -> None:
        self._shallow.clear()


def _elided(payload: dict) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("turns"), list):
        return payload
    return {**payload, "turns": elide_turns(payload["turns"])}
