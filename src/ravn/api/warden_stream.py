"""In-process SSE fan-out for persisted warden updates."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass

from ravn.warden.models import WardenSpec


@dataclass(frozen=True)
class WardenStreamEvent:
    """One SSE-ready warden update."""

    event: str
    warden: WardenSpec


class WardenStreamBroker:
    """Fan out per-warden updates to in-process subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[WardenStreamEvent]]] = defaultdict(set)

    def subscribe(self, warden_id: str) -> asyncio.Queue[WardenStreamEvent]:
        queue: asyncio.Queue[WardenStreamEvent] = asyncio.Queue()
        self._subscribers[warden_id].add(queue)
        return queue

    def unsubscribe(self, warden_id: str, queue: asyncio.Queue[WardenStreamEvent]) -> None:
        subscribers = self._subscribers.get(warden_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(warden_id, None)

    async def publish(self, event: str, warden: WardenSpec) -> None:
        for queue in tuple(self._subscribers.get(warden.id, ())):
            await queue.put(WardenStreamEvent(event=event, warden=warden))
