"""Background realm health polling."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress

from yggdrasil.connection_manager import ConnectionManager
from yggdrasil.models import RealmAdapter, RealmSummary

logger = logging.getLogger(__name__)


class RealmPoller:
    """Poll configured realm adapters and fan out changed summaries."""

    def __init__(
        self,
        adapters: Sequence[RealmAdapter],
        connection_manager: ConnectionManager,
        poll_interval: float,
    ) -> None:
        self._adapters = tuple(adapters)
        self._connection_manager = connection_manager
        self._poll_interval = poll_interval
        self._summaries: dict[str, RealmSummary] = {}
        self._task: asyncio.Task[None] | None = None

    def summary_for(self, realm_id: str) -> RealmSummary | None:
        """Return the most recently successful summary for a configured realm."""
        return self._summaries.get(realm_id)

    async def poll_once(self) -> None:
        """Poll every adapter, preserving other realms if one adapter fails."""
        for adapter in self._adapters:
            try:
                summary = await adapter.get_summary()
            except Exception:
                logger.warning("could not poll realm %s", adapter.realm_id, exc_info=True)
                continue

            previous = self._summaries.get(summary.realm_id)
            self._summaries[summary.realm_id] = summary
            if previous == summary:
                continue
            self._connection_manager.broadcast(summary)

    def start(self) -> None:
        """Start the configured recurring polling task."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="yggdrasil-realm-poller")

    async def stop(self) -> None:
        """Stop the recurring polling task."""
        if self._task is None:
            return

        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval)
            await self.poll_once()
