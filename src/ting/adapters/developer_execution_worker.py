"""Lifecycle runner for durable developer child launch and reconciliation."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING

from ting.domain.services.developer_execution import DeveloperExecutionService

if TYPE_CHECKING:
    from ting.domain.services.developer_delivery_wait import DeveloperDeliveryWaitService

logger = logging.getLogger(__name__)


class DeveloperExecutionWorker:
    def __init__(
        self,
        *,
        service: DeveloperExecutionService,
        interval_seconds: float,
        delivery_wait_service: DeveloperDeliveryWaitService | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("developer execution interval must be positive")
        self._service = service
        self._interval_seconds = interval_seconds
        self._delivery_wait_service = delivery_wait_service
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="ting-developer-execution")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._service.reconcile()
                if self._delivery_wait_service is not None:
                    await self._delivery_wait_service.reconcile()
                await self._service.launch_ready()
            except Exception:
                logger.exception(
                    "Developer execution reconciliation cycle failed; durable intents remain "
                    "eligible for retry"
                )
            await asyncio.sleep(self._interval_seconds)
