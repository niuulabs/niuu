"""Connection tracking and fan-out for realm summary streams."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from yggdrasil.models import RealmSummary


class SSEClientLimitExceededError(Exception):
    """Raised when accepting another SSE client would exceed the configured limit."""


class ConnectionManager:
    """Track active realm SSE subscriptions and broadcast changed summaries."""

    def __init__(self, max_clients: int) -> None:
        self._max_clients = max_clients
        self._connections: dict[str, set[asyncio.Queue[RealmSummary | None]]] = defaultdict(set)

    @property
    def max_clients(self) -> int:
        """Return the configured concurrent SSE client limit."""
        return self._max_clients

    @property
    def active_client_count(self) -> int:
        """Return the number of active SSE client connections."""
        return sum(len(connections) for connections in self._connections.values())

    def connect(self, realm_id: str) -> asyncio.Queue[RealmSummary | None]:
        """Register and return a queue for a realm subscription."""
        if self.active_client_count >= self._max_clients:
            raise SSEClientLimitExceededError

        queue: asyncio.Queue[RealmSummary | None] = asyncio.Queue()
        self._connections[realm_id].add(queue)
        return queue

    def disconnect(self, realm_id: str, queue: asyncio.Queue[RealmSummary | None]) -> None:
        """Remove a realm subscription and terminate its queue consumer."""
        connections = self._connections.get(realm_id)
        if connections is None:
            return

        if queue not in connections:
            return

        connections.remove(queue)
        queue.put_nowait(None)
        if connections:
            return
        del self._connections[realm_id]

    def broadcast(self, realm_id: str, data: RealmSummary) -> None:
        """Deliver data to all clients subscribed to a realm."""
        for queue in self._connections.get(realm_id, set()):
            queue.put_nowait(data)
