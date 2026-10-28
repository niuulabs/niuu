"""What a brokered Ravn flock actually published, for the broker's flock tools.

Skuld only transports here: every entry is an outcome or failure a flock member
put on the mesh, attributed to the room participant it arrived from (never to a
persona name carried in the payload). The broker's agent reads this log to
learn what the flock produced; nothing in it is inferred or synthesised.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

FlockActivityKind = Literal["outcome", "error"]


@dataclass(frozen=True)
class FlockActivity:
    """One outcome or failure a flock member published."""

    seq: int
    kind: FlockActivityKind
    peer_id: str
    persona: str
    event_type: str = ""
    verdict: str = ""
    summary: str = ""
    valid: bool = True
    routing_only: bool = False
    task_id: str = ""
    error: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    observed_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in ("", {}, None)}


class FlockActivityLog:
    """Bounded, ordered log of flock activity with waiters for new entries."""

    def __init__(self, *, capacity: int) -> None:
        self._entries: deque[FlockActivity] = deque(maxlen=capacity)
        self._seq = 0
        self._changed = asyncio.Condition()

    @property
    def cursor(self) -> int:
        """Sequence of the newest entry; entries after it are new."""
        return self._seq

    async def record(
        self,
        *,
        kind: FlockActivityKind,
        peer_id: str,
        persona: str,
        **details: Any,
    ) -> FlockActivity:
        async with self._changed:
            self._seq += 1
            entry = FlockActivity(
                seq=self._seq,
                kind=kind,
                peer_id=peer_id,
                persona=persona,
                observed_at=time.time(),
                **details,
            )
            self._entries.append(entry)
            self._changed.notify_all()
        return entry

    def after(self, cursor: int, *, peer_id: str = "") -> list[FlockActivity]:
        return [
            entry
            for entry in self._entries
            if entry.seq > cursor and (not peer_id or entry.peer_id == peer_id)
        ]

    async def wait_after(
        self, cursor: int, *, peer_id: str = "", timeout_s: float
    ) -> list[FlockActivity]:
        """Entries after ``cursor``, waiting up to ``timeout_s`` for the first one.

        Returns an empty list when nothing arrived in time; the caller decides
        what that means and must say so rather than treat it as a result.
        """
        async with self._changed:
            try:
                await asyncio.wait_for(
                    self._changed.wait_for(lambda: bool(self.after(cursor, peer_id=peer_id))),
                    timeout=timeout_s,
                )
            except TimeoutError:
                return []
        return self.after(cursor, peer_id=peer_id)
