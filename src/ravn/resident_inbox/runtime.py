"""Classify resident inbox signals and connect them to memory/work."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ravn.domain.resident_continuation import ResidentPolicyObservation
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentWorkItemBackend,
)
from ravn.resident_portfolio import _merge_text, merge_objectives

from .classify import _MEMORY_CLASSIFICATIONS, _WORK_CLASSIFICATIONS, classify_inbox_signal
from .models import (
    ResidentInboxBackend,
    ResidentInboxConfig,
    ResidentInboxRun,
    ResidentInboxSignal,
    ResidentInboxStatus,
    ResidentInboxTriage,
)
from .routing import _inbox_next_action, _objective_for_signal, _operator_resolution_for_signal


class ResidentInboxRuntime:
    """Classify resident inbox signals and connect them to memory/work."""

    def __init__(
        self,
        *,
        inbox: ResidentInboxBackend,
        work: ResidentWorkItemBackend,
        memory: Any | None = None,
        config: ResidentInboxConfig | None = None,
    ) -> None:
        self._inbox = inbox
        self._work = work
        self._memory = memory
        self._config = config or ResidentInboxConfig()

    async def run(self, mandate: str) -> ResidentInboxRun:
        rows = await self._inbox.list_signals(
            status=ResidentInboxStatus.NEW.value,
            limit=max(1, self._config.max_signals_per_wake),
        )
        objectives = tuple(await self._work.list_objectives(mandate))
        portfolio = await self._work.read_portfolio(mandate) or ResidentPortfolio(mandate=mandate)
        triages: list[ResidentInboxTriage] = []
        refs: list[str] = []
        current_objectives = objectives
        for signal_ref, signal in rows:
            triage, current_objectives, portfolio, persisted = await self._process_signal(
                mandate,
                signal_ref,
                signal,
                current_objectives,
                portfolio,
            )
            triages.append(triage)
            refs.extend(persisted)
        if triages:
            refs.append(
                await self._inbox.append_decision(

                        f"{datetime.now(UTC).isoformat()} [resident_inbox] "
                        f"processed={len(triages)} "
                        f"decisions={','.join(item.decision for item in triages)}"

                )
            )
        return ResidentInboxRun(
            mandate=mandate,
            processed=tuple(triages),
            persisted_refs=tuple(refs),
            final_suggested_next_action=_inbox_next_action(triages),
        )

    async def _process_signal(
        self,
        mandate: str,
        signal_ref: str,
        signal: ResidentInboxSignal,
        objectives: tuple[ResidentObjective, ...],
        portfolio: ResidentPortfolio,
    ) -> tuple[
        ResidentInboxTriage,
        tuple[ResidentObjective, ...],
        ResidentPortfolio,
        tuple[str, ...],
    ]:
        classification, confidence, reason = classify_inbox_signal(signal)
        classified = signal.with_updates(
            classification=classification,
            confidence=confidence,
            reason=reason,
            processed_at=datetime.now(UTC),
        )
        refs: list[str] = []
        updated_objectives = objectives
        memory_ref = ""
        objective_ref = ""
        target_id = ""
        decision = ResidentInboxStatus.IGNORED.value
        operator_resolution = _operator_resolution_for_signal(classified, objectives)
        if operator_resolution is not None:
            objective = operator_resolution
            objective_ref = await self._work.write_objective(objective)
            refs.append(objective_ref)
            target_id = objective.id
            updated_objectives = tuple(
                item if item.id != objective.id else objective for item in objectives
            )
            _, portfolio = await self._persist_portfolio(portfolio, updated_objectives)
            decision = (
                ResidentInboxStatus.BLOCKED.value
                if objective.status == ResidentObjectiveStatus.BLOCKED.value
                else ResidentInboxStatus.ATTACHED.value
            )
            classified = classified.with_updates(
                status=decision,
                target_objective_id=objective.id,
                evidence_refs=(signal_ref,),
            )
        elif classification in _MEMORY_CLASSIFICATIONS:
            memory_ref = await self._write_memory_observation(classified)
            if memory_ref:
                refs.append(memory_ref)
            decision = ResidentInboxStatus.REMEMBERED.value
            classified = classified.with_updates(status=ResidentInboxStatus.REMEMBERED.value)
        elif classification in _WORK_CLASSIFICATIONS:
            objective, action = _objective_for_signal(
                mandate,
                signal_ref,
                classified,
                objectives,
                config=self._config,
            )
            if objective is not None:
                objective_ref = await self._work.write_objective(objective)
                refs.append(objective_ref)
                target_id = objective.id
                updated_objectives = tuple(
                    item if item.id != objective.id else objective
                    for item in objectives
                )
                if all(item.id != objective.id for item in objectives):
                    updated_objectives = (*objectives, objective)
                _, portfolio = await self._persist_portfolio(portfolio, updated_objectives)
                decision = action
                classified = classified.with_updates(
                    status=action,
                    target_objective_id=objective.id,
                    evidence_refs=(signal_ref,),
                )
            else:
                decision = ResidentInboxStatus.IGNORED.value
                classified = classified.with_updates(status=ResidentInboxStatus.IGNORED.value)
        else:
            classified = classified.with_updates(status=ResidentInboxStatus.IGNORED.value)

        refs.append(await self._inbox.write_signal(classified))
        triage = ResidentInboxTriage(
            signal_id=classified.id,
            classification=classification,
            decision=decision,
            reason=reason,
            signal_ref=signal_ref,
            objective_ref=objective_ref,
            memory_ref=memory_ref,
            target_objective_id=target_id,
        )
        refs.append(await self._inbox.write_triage(triage))
        return triage, updated_objectives, portfolio, tuple(refs)

    async def _write_memory_observation(self, signal: ResidentInboxSignal) -> str:
        if self._memory is None or not hasattr(self._memory, "write_policy_observation"):
            return ""
        observation = ResidentPolicyObservation(
            subject=f"resident-inbox:{signal.classification}",
            observation=signal.summary,
            source=signal.source,
            status="candidate",
        )
        return await self._memory.write_policy_observation(observation)

    async def _persist_portfolio(
        self,
        portfolio: ResidentPortfolio,
        objectives: tuple[ResidentObjective, ...],
    ) -> tuple[str, ResidentPortfolio]:
        updated = portfolio.with_objectives(
            merge_objectives(objectives),
            decision_history=_merge_text(
                portfolio.decision_history,
                (
                    f"{datetime.now(UTC).isoformat()} "
                    "[resident_inbox] converted inbox signal into resident work",
                ),
                keep_last=True,
            ),
        )
        ref = await self._work.write_portfolio(updated)
        return ref, updated
