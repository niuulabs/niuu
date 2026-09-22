"""Lifecycle runner for durable child launch and reconciliation.

Drives an arbitrary list of execution reconcilers — the generic pack's own
service, plus any specialization's (code delivery's, today) — and one shared
wait reconciler, every cycle. Each reconciler's own repository decides which
rows it owns (see ``PostgresWorkflowExecutionRepository._ownership_predicate``
and its delivery override), so this worker never branches on what
distinguishes one service from another.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from ting.domain.services.workflow_wait import WorkflowWaitService

logger = logging.getLogger(__name__)


class ExecutionReconciler(Protocol):
    """Structural contract for one pack's own execution/child reconciliation loop."""

    async def reconcile(self, execution_id: UUID | None = None) -> dict[str, int]: ...

    async def launch_ready(self) -> int: ...


class WorkflowExecutionWorker:
    def __init__(
        self,
        *,
        services: Sequence[ExecutionReconciler],
        interval_seconds: float,
        wait_service: WorkflowWaitService | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("developer execution interval must be positive")
        if not services:
            raise ValueError("workflow execution worker requires at least one service")
        self._services = tuple(services)
        self._interval_seconds = interval_seconds
        self._wait_service = wait_service
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
            for index, service in enumerate(self._services):
                await self._run_phase(f"reconcile[{index}]", service.reconcile)
            if self._wait_service is not None:
                await self._run_phase("wait reconcile", self._wait_service.reconcile)
            for index, service in enumerate(self._services):
                await self._run_phase(f"launch_ready[{index}]", service.launch_ready)
            await asyncio.sleep(self._interval_seconds)

    async def _run_phase(self, name: str, phase: Callable[[], Awaitable[object]]) -> None:
        try:
            await phase()
        except Exception:
            logger.exception(
                "Developer execution %s phase failed; durable intents remain eligible "
                "for retry next cycle",
                name,
            )
