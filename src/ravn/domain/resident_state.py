"""Canonical resident state boundary."""

from __future__ import annotations

from typing import Protocol

from ravn.domain.resident_continuation import (
    ResidentA2ATaskRecord,
    ResidentBudgetSnapshot,
    ResidentDecisionStreakRecord,
    ResidentMemoryEntry,
    ResidentPolicyDecisionRecord,
    ResidentPolicyObservation,
    ResidentScheduledWakeRecord,
    ResidentTurnRecord,
    ResidentWorkingStateRecord,
)


class ResidentStatePort(Protocol):
    """Durable resident state boundary used by resident memory consumers.

    This is intentionally storage-neutral; concrete stores belong behind
    adapters, never in the port name or contract.
    """

    async def available(self) -> bool:
        """Whether this adapter's backing store is usable in the current environment.

        Checked at startup: a configured adapter whose backend is unusable is
        a hard failure, not a cue to substitute a different store.
        """
        ...

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]: ...

    async def read(self, ref: str) -> ResidentMemoryEntry | None: ...

    async def read_working_state(self, resident_id: str) -> ResidentMemoryEntry | None: ...

    async def write_turn(self, record: ResidentTurnRecord) -> str: ...

    async def write_working_state(self, record: ResidentWorkingStateRecord) -> str: ...

    async def read_decision_streak(self, resident_id: str) -> ResidentDecisionStreakRecord | None:
        """Return the resident's current repeated-decision streak, if any."""
        ...

    async def count_cases(self) -> tuple[int, int] | None:
        """Return (live, total) durable case counts, or None when the store
        cannot answer without walking its whole backend.

        None is an answer, not a degradation: health surfaces simply omit the
        case gauges for such a store, the same way corpus gauges only exist
        for memory backends that can sample them cheaply.
        """
        ...

    async def write_decision_streak(self, record: ResidentDecisionStreakRecord) -> str: ...

    async def read_a2a_task(self, task_id: str) -> ResidentMemoryEntry | None: ...

    async def write_a2a_task(self, record: ResidentA2ATaskRecord) -> str: ...

    async def list_a2a_tasks(self) -> list[ResidentMemoryEntry]: ...

    async def write_budget(self, snapshot: ResidentBudgetSnapshot) -> str: ...

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str: ...

    async def list_policy_observations(self) -> list[ResidentPolicyObservation]: ...

    async def write_policy_decision(self, decision: ResidentPolicyDecisionRecord) -> str: ...

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
        case_id: str = "",
        turn_ref: str = "",
    ) -> str: ...

    async def read_operator_needed(self, case_id: str = "") -> ResidentMemoryEntry | None: ...

    async def write_operator_answer(self, answer: str, *, case_id: str = "") -> str: ...

    async def read_operator_answer(self, case_id: str = "") -> ResidentMemoryEntry | None: ...

    async def consume_operator_answer(self, answer: ResidentMemoryEntry) -> str: ...

    async def list_operator_needed(self) -> list[ResidentMemoryEntry]: ...

    async def list_operator_answers(self) -> list[ResidentMemoryEntry]: ...

    async def write_scheduled_wake(self, record: ResidentScheduledWakeRecord) -> str: ...

    async def list_scheduled_wakes(self) -> list[ResidentMemoryEntry]: ...

    async def consume_scheduled_wake(self, wake: ResidentMemoryEntry) -> str: ...

    async def list_refs(self, prefix: str = "") -> list[str]: ...
