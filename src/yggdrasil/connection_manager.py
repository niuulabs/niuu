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
        self._connections: dict[str, set[asyncio.Queue[RealmSummary]]] = defaultdict(set)

    @property
    def max_clients(self) -> int:
        """Return the configured concurrent SSE client limit."""
        return self._max_clients

    @property
    def active_client_count(self) -> int:
        """Return the number of active SSE client connections."""
        return sum(len(connections) for connections in self._connections.values())

    def connect(self, realm_id: str) -> asyncio.Queue[RealmSummary]:
        """Register and return a queue for a realm subscription."""
        if self.active_client_count >= self._max_clients:
            raise SSEClientLimitExceededError

        queue: asyncio.Queue[RealmSummary] = asyncio.Queue()
        self._connections[realm_id].add(queue)
        return queue

    def disconnect(self, realm_id: str, queue: asyncio.Queue[RealmSummary]) -> None:
        """Remove a realm subscription after a client disconnects."""
        connections = self._connections.get(realm_id)
        if connections is None:
            return

        connections.discard(queue)
        if connections:
            return
        del self._connections[realm_id]

    def broadcast(self, summary: RealmSummary) -> None:
        """Deliver a changed summary to all clients subscribed to its realm."""
        for queue in self._connections.get(summary.realm_id, set()):
            queue.put_nowait(summary)
