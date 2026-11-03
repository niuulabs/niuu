"""Which Guild node owns a session, as this facade last saw it.

Without it, reading a session another node owns asks every node, Spark included, on
every read. The memory is learned from what the facade already sees (Guild-scope
listings, owner lookups that found the session) and is only a hint: an entry lives for
``ttl_seconds``, and any failure from the remembered node drops it and the full search
runs, exactly as without the memory, so a hint never hides a session. Entries are
bounded by the sessions seen within two TTLs: expired ones are pruned at most once per
TTL, when the memory learns.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable


class SessionOwners:
    def __init__(self, ttl_seconds: float, *, clock: Callable[[], float] = time.monotonic):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = ttl_seconds
        self._clock = clock
        self._owners: dict[str, tuple[str, float]] = {}
        self._next_prune = clock() + ttl_seconds

    def owner_of(self, session_id: str) -> str | None:
        """The instance id last seen owning ``session_id``, while it is fresh."""
        entry = self._owners.get(session_id)
        if entry is None:
            return None
        instance_id, expires = entry
        if expires <= self._clock():
            del self._owners[session_id]
            return None
        return instance_id

    def learn(self, pairs: Iterable[tuple[str, str]]) -> None:
        """Remember ``(session_id, instance_id)`` pairs; prune what has expired."""
        now = self._clock()
        expires = now + self._ttl
        for session_id, instance_id in pairs:
            if session_id and instance_id:
                self._owners[session_id] = (instance_id, expires)
        if now < self._next_prune:
            return
        self._next_prune = now + self._ttl
        for session_id in [s for s, (_, until) in self._owners.items() if until <= now]:
            del self._owners[session_id]

    def forget(self, session_id: str, instance_id: str | None = None) -> None:
        """Drop the entry; with ``instance_id``, only while it still names that node
        (something newer may have learned another owner meanwhile)."""
        entry = self._owners.get(session_id)
        if entry is not None and instance_id in (None, entry[0]):
            del self._owners[session_id]

    def __len__(self) -> int:
        return len(self._owners)
