"""ResidentAutonomyTrigger — daemon bridge for resident autonomy wake passes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ravn.domain.models import AgentTask
from ravn.domain.operator_contact import (
    OperatorContactKind,
    OperatorContactPort,
    OperatorContactPurpose,
    OperatorContactRequest,
    OperatorContactResult,
    OperatorContactStatus,
)
from ravn.domain.resident_portfolio import (
    ResidentDelegationStatus,
    ResidentObjectiveStatus,
    ResidentWorkItemBackend,
)
from ravn.domain.wakeful_resident import WakefulResidentMemoryPort
from ravn.ports.trigger import TriggerPort
from ravn.resident_portfolio import (
    _OPERATOR_CONTACT_PREFIX,
    ResidentAutonomyLoopConfig,
    ResidentAutonomyLoopRuntime,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResidentWakeAttention:
    """A resident wake candidate and the reason it deserves attention now."""

    name: str
    should_run: bool
    priority: int = 0
    reason: str = ""
    observations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResidentWakeExtension:
    """One resident layer pass that can compete for wake attention."""

    name: str
    run: Callable[[str], Awaitable[Any]]
    inspect: Callable[[str], Awaitable[ResidentWakeAttention]] | None = None
    priority: int = 10
    reason: str = "configured resident wake extension"


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
        operator_memory: Any | None = None,
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
        self._operator_memory = operator_memory
        self._ask_operator = ask_operator
        self._portfolio_manager = portfolio_manager
        self._wake_extensions = tuple(wake_extensions)
        self._loop_config = loop_config or ResidentAutonomyLoopConfig()
        self._poll_interval_seconds = max(0.0, poll_interval_seconds)
        self._initial_delay_seconds = max(0.0, initial_delay_seconds)
        self._skip_when_operator_pending = skip_when_operator_pending
        self._emitted_operator_contact_ids: set[str] = set()

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
            pending_marker = await self._pending_operator_marker_input()
            if pending_marker is not None:
                contact_ref = await self._contact_pending_operator_input(pending_marker)
                await self._backend.append_decision(
                    self._mandate,
                    (
                        f"{datetime.now(UTC).isoformat()} [resident_autonomy_trigger] "
                        f"slept: pending operator input for {pending_marker['ref']}: "
                        f"{pending_marker['question']}"
                        f"{'; contact_ref=' + contact_ref if contact_ref else ''}"
                    ),
                )
                logger.info(
                    "ResidentAutonomyTrigger: sleeping while continuation operator input "
                    "is pending (ref=%s)",
                    pending_marker["ref"],
                )
                return None

        extension = await self._select_wake_extension()
        if extension is not None:
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
            if bool(getattr(result, "operator_pending", False)):
                logger.info(
                    "ResidentAutonomyTrigger: wake extension %s is waiting for operator input",
                    extension.name,
                )
                return result

        if self._skip_when_operator_pending:
            pending = await self._pending_operator_input()
            if pending is not None:
                contact_ref = await self._contact_pending_operator_input(pending)
                if not await self._has_resident_work():
                    await self._backend.append_decision(
                        self._mandate,
                        (
                            f"{datetime.now(UTC).isoformat()} [resident_autonomy_trigger] "
                            f"slept: pending operator input for {pending['ref']}: "
                            f"{pending['question']}"
                            f"{'; contact_ref=' + contact_ref if contact_ref else ''}"
                        ),
                    )
                    logger.info(
                        "ResidentAutonomyTrigger: sleeping while operator input is pending "
                        "(ref=%s)",
                        pending["ref"],
                    )
                    return None
                await self._backend.append_decision(
                    self._mandate,
                    (
                        f"{datetime.now(UTC).isoformat()} [resident_autonomy_trigger] "
                        f"contacted pending operator input for {pending['ref']} and "
                        "continued with other resident work"
                        f"{'; contact_ref=' + contact_ref if contact_ref else ''}"
                    ),
                )
                logger.info(
                    "ResidentAutonomyTrigger: operator input is pending for %s; "
                    "continuing with other resident work",
                    pending["ref"],
                )

        if self._portfolio_manager is not None:
            portfolio_run = await self._portfolio_manager.run(self._mandate)
            logger.info(
                "ResidentAutonomyTrigger: portfolio pass decision=%s selected=%d advanced=%d",
                getattr(portfolio_run.decision, "value", portfolio_run.decision),
                len(portfolio_run.selected_objectives),
                len(portfolio_run.advanced_objectives),
            )

        if not await self._has_resident_work():
            await self._backend.append_decision(
                self._mandate,
                (
                    f"{datetime.now(UTC).isoformat()} [resident_autonomy_trigger] "
                    "slept: no resident work currently needs autonomy loop attention"
                ),
            )
            logger.info("ResidentAutonomyTrigger: sleeping; no resident work is available")
            return None

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

    async def _contact_pending_operator_input(self, pending: dict[str, str]) -> str:
        if self._ask_operator is None:
            return ""
        objective = await self._pending_operator_objective()
        request = _pending_operator_request(pending, objective)
        if request.id in self._emitted_operator_contact_ids:
            return ""
        if await self._operator_contact_already_emitted(request.id):
            self._emitted_operator_contact_ids.add(request.id)
            return ""
        try:
            result = await self._ask_operator.ask(request)
        except Exception as exc:
            logger.warning(
                "ResidentAutonomyTrigger: failed to emit pending operator contact",
                exc_info=True,
            )
            result = OperatorContactResult(
                request=request,
                status=OperatorContactStatus.FAILED.value,
                answer=str(exc),
                approved=False,
                responded_at=datetime.now(UTC),
            )
        self._emitted_operator_contact_ids.add(request.id)
        return await self._backend.write_operator_contact(result)

    async def _operator_contact_already_emitted(self, contact_id: str) -> bool:
        if not contact_id:
            return False
        if not hasattr(self._backend, "list_refs"):
            return False
        try:
            refs = await self._backend.list_refs(_OPERATOR_CONTACT_PREFIX)
        except Exception:
            logger.warning(
                "ResidentAutonomyTrigger: failed to inspect existing operator contacts",
                exc_info=True,
            )
            return False
        expected = f"{_OPERATOR_CONTACT_PREFIX}/{contact_id}.md"
        return expected in set(refs)

    async def _select_wake_extension(self) -> ResidentWakeExtension | None:
        if not self._wake_extensions:
            return None

        candidates: list[tuple[ResidentWakeAttention, ResidentWakeExtension]] = []
        for extension in self._wake_extensions:
            attention = await self._inspect_wake_extension(extension)
            candidates.append((attention, extension))

        runnable = [item for item in candidates if item[0].should_run]
        runnable.sort(key=lambda item: item[0].priority, reverse=True)
        selected = runnable[0] if runnable else None
        candidate_summary = "; ".join(
            (
                f"{attention.name}:run={attention.should_run}:"
                f"priority={attention.priority}:reason={attention.reason}"
            )
            for attention, _extension in candidates
        )
        selected_name = selected[1].name if selected is not None else "none"
        selected_reason = (
            selected[0].reason
            if selected is not None
            else "no candidate needed attention"
        )
        await self._backend.append_decision(
            self._mandate,
            (
                f"{datetime.now(UTC).isoformat()} [resident_attention] "
                f"selected={selected_name} reason={selected_reason} "
                f"candidates={candidate_summary}"
            ),
        )
        if selected is None:
            return None
        return selected[1]

    async def _inspect_wake_extension(
        self,
        extension: ResidentWakeExtension,
    ) -> ResidentWakeAttention:
        if extension.inspect is None:
            return ResidentWakeAttention(
                name=extension.name,
                should_run=True,
                priority=extension.priority,
                reason=extension.reason,
            )
        attention = await extension.inspect(self._mandate)
        if attention.name != extension.name:
            return ResidentWakeAttention(
                name=extension.name,
                should_run=attention.should_run,
                priority=attention.priority,
                reason=attention.reason,
                observations=attention.observations,
            )
        return attention

    async def _pending_operator_input(self) -> dict[str, str] | None:
        pending_objective = await self._pending_operator_objective()
        if pending_objective is not None:
            return {
                "ref": str(pending_objective.id),
                "question": str(
                    pending_objective.pending_question or pending_objective.title
                ),
            }
        if self._operator_memory is None or not hasattr(
            self._operator_memory,
            "read_operator_needed",
        ):
            return None
        return await self._pending_operator_marker_input()

    async def _pending_operator_marker_input(self) -> dict[str, str] | None:
        if self._operator_memory is None or not hasattr(
            self._operator_memory,
            "read_operator_needed",
        ):
            return None
        pending_marker = await self._operator_memory.read_operator_needed()
        if pending_marker is None:
            return None
        return {
            "ref": str(getattr(pending_marker, "path", "") or "resident operator-needed"),
            "question": _operator_marker_question(str(getattr(pending_marker, "content", ""))),
        }

    async def _pending_operator_objective(self) -> Any | None:
        objectives = await self._backend.list_objectives(self._mandate)
        for objective in objectives:
            if objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value:
                return objective
        return None

    async def _has_resident_work(self) -> bool:
        objectives = await self._backend.list_objectives(self._mandate)
        if any(_objective_needs_autonomy_loop(objective) for objective in objectives):
            return True
        if not hasattr(self._backend, "list_delegations"):
            return False
        delegations = await self._backend.list_delegations(self._mandate)
        return any(_delegation_needs_autonomy_loop(delegation) for delegation in delegations)


def _operator_marker_question(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- question:"):
            return stripped.split(":", 1)[1].strip() or "operator input needed"
    return "operator input needed"


def _pending_objective_operator_request(
    objective: Any,
    question: str,
) -> OperatorContactRequest:
    objective_id = str(getattr(objective, "id", "") or "").strip()
    risks = tuple(str(item) for item in getattr(objective, "risk_boundaries", ()) or ())
    return OperatorContactRequest(
        id=f"operator-contact-{objective_id}" if objective_id else "operator-contact",
        question=question,
        reason="Resident objective needs operator judgment before the daemon continues.",
        impact=(
            "A positive answer allows this specific objective to continue; "
            "no answer keeps the resident waiting."
        ),
        kind=OperatorContactKind.HELP_NEEDED,
        purpose=OperatorContactPurpose.APPROVAL if risks else OperatorContactPurpose.CLARIFICATION,
        source_objective_id=objective_id,
        risk_boundaries=risks,
    )


def _pending_operator_request(
    pending: dict[str, str],
    objective: Any | None,
) -> OperatorContactRequest:
    question = str(pending.get("question") or "").strip() or "operator input needed"
    if objective is not None:
        return _pending_objective_operator_request(objective, question)
    ref = str(pending.get("ref") or "resident operator-needed").strip()
    contact_id = _contact_id_from_pending_ref(ref)
    return OperatorContactRequest(
        id=contact_id,
        question=question,
        reason="Resident continuation is waiting for operator input before the daemon continues.",
        impact=(
            "A reply is persisted into resident memory and lets the resident resume the "
            "blocked work; no reply keeps the resident sleeping."
        ),
        kind=OperatorContactKind.HELP_NEEDED,
        purpose=OperatorContactPurpose.CLARIFICATION,
        source_objective_id="",
        risk_boundaries=(),
        help_needed_outcome={
            "operator_needed_ref": ref,
            "pending_question": question,
        },
    )


def _contact_id_from_pending_ref(ref: str) -> str:
    safe = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-"
        for ch in str(ref or "resident-operator-needed").strip().casefold()
    ).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    return f"operator-contact-{safe or 'resident-operator-needed'}"


def _objective_needs_autonomy_loop(objective: Any) -> bool:
    return str(getattr(objective, "status", "")) not in {
        ResidentObjectiveStatus.COMPLETED.value,
        ResidentObjectiveStatus.BLOCKED.value,
        ResidentObjectiveStatus.CANCELLED.value,
        ResidentObjectiveStatus.SUPERSEDED.value,
        ResidentObjectiveStatus.NEEDS_OPERATOR.value,
    }


def _delegation_needs_autonomy_loop(delegation: Any) -> bool:
    return str(getattr(delegation, "status", "")) not in {
        ResidentDelegationStatus.COMPLETED.value,
        ResidentDelegationStatus.CANCELLED.value,
        ResidentDelegationStatus.FAILED.value,
        ResidentDelegationStatus.BLOCKED.value,
        ResidentDelegationStatus.NEEDS_OPERATOR.value,
        ResidentDelegationStatus.UNAVAILABLE.value,
    }
