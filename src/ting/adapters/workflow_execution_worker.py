"""Lifecycle runner for durable developer child launch and reconciliation."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING

from ting.domain.services.delivery_execution import DeliveryExecutionService

if TYPE_CHECKING:
    from ting.domain.services.workflow_wait import WorkflowWaitService

logger = logging.getLogger(__name__)


class WorkflowExecutionWorker:
    def __init__(
        self,
        *,
        service: DeliveryExecutionService,
        interval_seconds: float,
        delivery_wait_service: WorkflowWaitService | None = None,
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
        self._task = asyncio.create_task(self._run(), name="ting-workflow-execution")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            # Each phase is isolated: one phase raising must not skip the
            # others in the same cycle. A phase's own durable state
            # (leases, poll ordering, failure counters) is what makes the
            # next cycle's retry safe, so every phase still gets to run.
            await self._run_phase("reconcile", self._service.reconcile)
            if self._delivery_wait_service is not None:
                await self._run_phase(
                    "delivery wait reconcile", self._delivery_wait_service.reconcile
                )
            await self._run_phase("launch_ready", self._service.launch_ready)
            await asyncio.sleep(self._interval_seconds)

    async def _run_phase(self, name: str, phase) -> None:
        try:
            await phase()
        except Exception:
            logger.exception(
                "Developer execution %s phase failed; durable intents remain eligible "
                "for retry next cycle",
                name,
            )
