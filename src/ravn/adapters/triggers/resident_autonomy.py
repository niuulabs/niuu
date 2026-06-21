"""ResidentAutonomyTrigger — daemon bridge for resident autonomy wake passes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ravn.domain.models import AgentTask
from ravn.domain.operator_contact import OperatorContactPort
from ravn.domain.resident_portfolio import ResidentObjectiveStatus, ResidentWorkItemBackend
from ravn.domain.wakeful_resident import WakefulResidentMemoryPort
from ravn.ports.trigger import TriggerPort
from ravn.resident_portfolio import ResidentAutonomyLoopConfig, ResidentAutonomyLoopRuntime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResidentWakeExtension:
    """One ordered resident layer pass run inside the daemon wake sequence."""

    name: str
    run: Callable[[str], Awaitable[Any]]


class ResidentAutonomyTrigger(TriggerPort):
    """Run the resident autonomy kernel from the existing daemon drive loop.

    The trigger owns no process lifetime of its own; ``DriveLoop`` starts and
    cancels it alongside every other daemon trigger.  Each poll performs one
    bounded resident wake pass using the configured resident backend/executor.
    """

    def __init__(
        self,
        *,
        mandate: str,
        backend: ResidentWorkItemBackend,
        executor: Any,
        wake_memory: WakefulResidentMemoryPort | None = None,
        expert_memory: Any | None = None,
        ask_operator: OperatorContactPort | None = None,
        portfolio_manager: Any | None = None,
        wake_extensions: Sequence[ResidentWakeExtension] = (),
        loop_config: ResidentAutonomyLoopConfig | None = None,
        poll_interval_seconds: float = 300.0,
        initial_delay_seconds: float = 0.0,
        skip_when_operator_pending: bool = True,
    ) -> None:
        self._mandate = mandate.strip()
        self._backend = backend
        self._executor = executor
        self._wake_memory = wake_memory
        self._expert_memory = expert_memory
        self._ask_operator = ask_operator
        self._portfolio_manager = portfolio_manager
        self._wake_extensions = tuple(wake_extensions)
        self._loop_config = loop_config or ResidentAutonomyLoopConfig()
        self._poll_interval_seconds = max(0.0, poll_interval_seconds)
        self._initial_delay_seconds = max(0.0, initial_delay_seconds)
        self._skip_when_operator_pending = skip_when_operator_pending

    @property
    def name(self) -> str:
        return "resident_autonomy"

    async def run(self, enqueue: Callable[[AgentTask], Awaitable[None]]) -> None:
        """Poll forever until cancelled by ``DriveLoop``."""
        del enqueue
        if not self._mandate:
            logger.warning("ResidentAutonomyTrigger: disabled because mandate is empty")
            return
        if self._initial_delay_seconds > 0:
            await asyncio.sleep(self._initial_delay_seconds)

        logger.info(
            "ResidentAutonomyTrigger: starting (poll=%.1fs, cycles=%d)",
            self._poll_interval_seconds,
            self._loop_config.max_cycles,
        )
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ResidentAutonomyTrigger: wake pass failed")

            await asyncio.sleep(self._poll_interval_seconds)

    async def run_once(self) -> Any:
        """Run one daemon resident wake pass and persist an audit decision."""
        if not self._mandate:
            raise ValueError("resident autonomy mandate must not be empty")

        if self._skip_when_operator_pending:
            pending = await self._pending_operator_objective()
            if pending is not None:
                ref = await self._backend.append_decision(
                    self._mandate,
                    (
                        f"{datetime.now(UTC).isoformat()} [resident_autonomy_trigger] "
                        f"slept: pending operator input for {pending.id}: "
                        f"{pending.pending_question or pending.title}"
                    ),
                )
                logger.info(
                    "ResidentAutonomyTrigger: sleeping while operator input is pending "
                    "(objective=%s, ref=%s)",
                    pending.id,
                    ref,
                )
                return None

        for extension in self._wake_extensions:
            result = await extension.run(self._mandate)
            refs = tuple(getattr(result, "persisted_refs", ()) or ())
            logger.info(
                "ResidentAutonomyTrigger: wake extension %s completed (refs=%d, next=%s)",
                extension.name,
                len(refs),
                getattr(result, "final_suggested_next_action", ""),
            )
            await self._backend.append_decision(
                self._mandate,
                (
                    f"{datetime.now(UTC).isoformat()} [resident_wake_extension] "
                    f"{extension.name} refs={len(refs)} "
                    f"next={getattr(result, 'final_suggested_next_action', '')}"
                ),
            )

        if self._portfolio_manager is not None:
            portfolio_run = await self._portfolio_manager.run(self._mandate)
            logger.info(
                "ResidentAutonomyTrigger: portfolio pass decision=%s selected=%d advanced=%d",
                getattr(portfolio_run.decision, "value", portfolio_run.decision),
                len(portfolio_run.selected_objectives),
                len(portfolio_run.advanced_objectives),
            )

        run = await ResidentAutonomyLoopRuntime(
            backend=self._backend,
            executor=self._executor,
            ask_operator=self._ask_operator,
            wake_memory=self._wake_memory,
            expert_memory=self._expert_memory,
            config=self._loop_config,
        ).run(self._mandate)
        await self._backend.append_decision(
            self._mandate,
            (
                f"{datetime.now(UTC).isoformat()} [resident_autonomy_trigger] "
                f"cycles={len(run.cycles)} refs={len(run.persisted_refs)} "
                f"questions={len(run.operator_questions)} "
                f"contacts={len(run.operator_contacts)} "
                f"next={run.final_suggested_next_action}"
            ),
        )
        logger.info(
            "ResidentAutonomyTrigger: completed wake pass (cycles=%d, refs=%d, contacts=%d)",
            len(run.cycles),
            len(run.persisted_refs),
            len(run.operator_contacts),
        )
        return run

    async def _pending_operator_objective(self) -> Any | None:
        objectives = await self._backend.list_objectives(self._mandate)
        for objective in objectives:
            if objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value:
                return objective
        return None
