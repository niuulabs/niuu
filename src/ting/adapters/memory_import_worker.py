"""The loop that runs every memory import on its schedule."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from niuu.observability import get_observability
from ting.domain.services.memory_imports import MemoryImportService

logger = logging.getLogger(__name__)

#: Counter of loop passes that could not even list the imports.
LOOP_FAILURES = "ting.memory.import.loop_failures"


class MemoryImportWorker:
    """Runs all imports, then waits ``interval_seconds``, for as long as Ting runs.

    An import that fails is recorded on the import, logged and counted by the service and
    runs again on the next pass. A pass that cannot list the imports (the database is down)
    is logged at error and counted, and the next pass tries again.
    """

    def __init__(self, *, service: MemoryImportService, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("memory_imports.poll_seconds must be positive")
        self._service = service
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="ting-memory-imports")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self._interval_seconds)

    async def run_once(self) -> tuple[int, int]:
        """One pass over every import: ``(succeeded, failed)``."""
        try:
            return await self._service.run_all()
        except Exception:
            logger.error(
                "Memory import pass could not run; trying again in %.0fs",
                self._interval_seconds,
                exc_info=True,
            )
            get_observability().count(
                LOOP_FAILURES, description="Import passes that could not list the imports."
            )
            return 0, 0
