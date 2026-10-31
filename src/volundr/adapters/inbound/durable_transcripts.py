"""Durable transcript rebuilds: at most one per session at a time, shallow results remembered.

A session without a reachable pod (stopped, or its pod down) is served from the durable
rebuild (``ForgeService.get_transcript``): every event of the session's log is read and
folded into turns. On a big session that is hundreds of MB of Python objects (43k events,
136 MB of log rows on Valhalla), and a client polls the conversation every few seconds,
often several requests at once. Each poll rebuilt from scratch, the rebuilds overlapped,
and the API was OOMKilled.

Two rules keep that bounded:

* **Single flight.** Rebuilds of one session run one at a time; a request that arrives
  while one runs waits for it (and, for shallow reads, is then served from the cache).
* **Shallow cache.** The shallow (tool_result-elided) rebuild is kept per session, keyed on
  the event log's ``MAX(seq)``. A stopped session's log does not grow, so every later
  shallow read is free; a new event invalidates the entry. Only the elided form is kept,
  never the full one, and only for the most recently used sessions. A session without an
  event log (seq 0: archive or workspace transcript) is never cached, as nothing says when
  it changed.

Full reads (tool-result lookups, the transcript download, ``detail=full``) are single-flight
but not cached: their payload is the heavy one.
"""

from __future__ import annotations

import asyncio
import weakref
from collections import OrderedDict
from typing import Any, Protocol
from uuid import UUID

from skuld.conversation_shallow import elide_turns

#: Sessions whose shallow transcript is kept. Elided transcripts are small, but a few
#: thousand turns of text still add up; this bounds the worst case.
DEFAULT_MAX_SESSIONS = 16


class _TranscriptSource(Protocol):
    async def get_transcript(self, session_id: UUID) -> dict:
        """The full durable transcript (``ForgeService.get_transcript``)."""

    async def durable_latest_seq(self, session_id: UUID) -> int:
        """The event log's ``MAX(seq)``, 0 without one."""


class DurableTranscripts:
    """Single-flight durable rebuilds, with the shallow result cached per (session, seq)."""

    def __init__(self, max_sessions: int = DEFAULT_MAX_SESSIONS) -> None:
        self._max_sessions = max_sessions
        self._shallow: OrderedDict[str, tuple[int, dict[str, Any]]] = OrderedDict()
        # One lock per session, alive only while someone holds or waits on it.
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def _lock(self, sid: str) -> asyncio.Lock:
        lock = self._locks.get(sid)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[sid] = lock
        return lock

    async def full(self, source: _TranscriptSource, session_id: UUID) -> dict:
        """The full durable transcript, rebuilt under the session's lock."""
        async with self._lock(str(session_id)):
            return await source.get_transcript(session_id)

    async def shallow(self, source: _TranscriptSource, session_id: UUID) -> dict:
        """The tool_result-elided durable transcript, rebuilt only when the event log grew."""
        sid = str(session_id)
        async with self._lock(sid):
            seq = await source.durable_latest_seq(session_id)
            hit = self._shallow.get(sid)
            if hit is not None and seq > 0 and hit[0] == seq:
                self._shallow.move_to_end(sid)
                return hit[1]
            payload = await source.get_transcript(session_id)
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
