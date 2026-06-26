"""Runtime classes that orchestrate resident portfolio management."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from ravn.domain.operator_contact import (
    OperatorContactResult,
    OperatorContactStatus,
)
from ravn.domain.resident_continuation import ResidentBudgetLimits, ResidentBudgetSnapshot
from ravn.domain.resident_expert import (
    ResidentDomainExpertMemoryPort,
    ResidentWorkstream,
)
from ravn.domain.resident_portfolio import (
    CapabilityDiscoveryPort,
    ResidentAutonomyCycleReport,
    ResidentAutonomyRun,
    ResidentCapabilityDiscoveryReport,
    ResidentCapabilityDiscoveryResult,
    ResidentDelegationRecord,
    ResidentDelegationReport,
    ResidentDelegationReview,
    ResidentDelegationReviewDecision,
    ResidentDelegationStatus,
    ResidentExecutionPort,
    ResidentExecutionResult,
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentPortfolioDecisionKind,
    ResidentPortfolioRepairRecord,
    ResidentPortfolioRun,
    ResidentPortfolioStewardActionKind,
    ResidentPortfolioStewardPassReport,
    ResidentPortfolioStewardRun,
    ResidentPortfolioValidationFinding,
    ResidentPortfolioValidationReport,
    ResidentWorkItemBackend,
)
from ravn.domain.wakeful_resident import (
    WakefulResidentCycleRecord,
    WakefulResidentDecisionKind,
    WakefulResidentMemoryPort,
    WakefulResidentRun,
)
from ravn.ports.operator_contact import OperatorContactPort
from ravn.resident_continuation import ResidentRunBudget, _compact_line
from ravn.resident_operator_contact import approved_risk_objective_ids_from_objectives

from .config import (
    ResidentAutonomyLoopConfig,
    ResidentCapabilityDiscoveryConfig,
    ResidentDelegationConfig,
    ResidentPortfolioConfig,
    ResidentPortfolioEvidence,
    ResidentPortfolioStewardConfig,
    WakefulRuntimePort,
)
from .constants import (
    _CAPABILITY_DISCOVERY_PREFIX,
    _DECISION_HISTORY_LIMIT,
    _DELEGATION_PREFIX,
    _DELEGATION_RESULT_PREFIX,
    _DELEGATION_REVIEW_PREFIX,
    _DOMAIN_MODEL_REF,
    _TERMINAL_DELEGATION_RESULT_STATUSES,
)
from .helpers import (
    _absorb_delegation_result,
    _active_unresolved_delegation,
    _active_unresolved_delegation_count,
    _autonomy_attention_reason,
    _autonomy_final_suggestion,
    _bootstrap_decision_entries,
    _consolidate_learning,
    _counts_by_status,
    _decision_entry,
    _delegation_cycle_findings,
    _delegation_cycle_work_items,
    _delegation_id,
    _delegation_keep_sort_key,
    _delegation_next_action,
    _delegation_operator_objective,
    _delegation_operator_question,
    _delegation_reason,
    _delegation_report_had_activity,
    _dependency_summary,
    _domain_model_with_capability_learning,
    _domain_model_with_delegation_learning,
    _dry_run_non_selection_reason,
    _finding,
    _follow_up_from_finding,
    _follow_ups_from_delegation_result,
    _gather_portfolio_links,
    _has_meaningful_items,
    _hints,
    _is_blocked_objective,
    _limit_discovery_options,
    _merge_and_persist_objectives,
    _merge_text,
    _objective_by_id,
    _objective_by_id_or_none,
    _objective_for_delegation_question,
    _objective_from_text,
    _objective_mandate,
    _objectives_from_capability_discovery,
    _operator_contact_request,
    _preview_objective,
    _repair_skip_reason,
    _result_usage,
    _review_objective_against_wake,
    _should_bootstrap_portfolio,
    _skipped_repair,
    _steward_decision_entry,
    _suggested_action,
    _sum_budget,
    _validate_objective_state,
    _validate_portfolio_links,
    _validate_required_fields,
    _validate_stable_id,
    _validate_status,
    build_worker_brief,
    detect_capability_gaps,
    discover_objectives,
    merge_objectives,
    prioritize_objectives,
    render_capability_discovery_result,
    render_delegation_result,
    render_delegation_review,
    review_delegation_result,
    select_delegation_candidates,
    select_objectives,
)


class ResidentLongHorizonWorkManager:
    """Manages a resident portfolio and advances selected bounded work."""

    def __init__(
        self,
        *,
        backend: ResidentWorkItemBackend,
        wake_runtime: WakefulRuntimePort,
        expert_memory: ResidentDomainExpertMemoryPort | None = None,
        wake_memory: WakefulResidentMemoryPort | None = None,
        config: ResidentPortfolioConfig | None = None,
    ) -> None:
        self._backend = backend
        self._wake_runtime = wake_runtime
        self._expert_memory = expert_memory
        self._wake_memory = wake_memory
        self._config = config or ResidentPortfolioConfig()

    async def run(self, mandate: str) -> ResidentPortfolioRun:
        budget = ResidentRunBudget(
            ResidentBudgetLimits(
                max_turns=self._config.max_objectives_selected,
                max_wall_clock_seconds=self._config.max_wall_clock_seconds,
                max_tokens=self._config.max_tokens,
            )
        )
        portfolio = await self._load_portfolio(mandate)
        evidence = await self._gather_evidence(mandate)
        discovered = discover_objectives(mandate, portfolio=portfolio, evidence=evidence)
        bootstrap_entries: tuple[str, ...] = ()
        if _should_bootstrap_portfolio(
            portfolio=portfolio,
            evidence=evidence,
            discovered=discovered,
            enabled=self._config.bootstrap_when_empty,
        ):
            bootstrap_run = await self._wake_runtime.run(mandate)
            bootstrap_entries = _bootstrap_decision_entries(bootstrap_run)
            evidence = await self._gather_evidence(mandate)
            discovered = discover_objectives(mandate, portfolio=portfolio, evidence=evidence)

        objectives = merge_objectives(portfolio.objectives + discovered)
        objectives = prioritize_objectives(objectives, mandate=mandate)
        selected = select_objectives(
            objectives,
            max_selected=self._config.max_objectives_selected,
            max_active=self._config.max_active_objectives,
        )
        advanced: list[ResidentObjective] = []
        selected_for_run: list[ResidentObjective] = []
        decision = ResidentPortfolioDecisionKind.SLEEP
        reason = "no ready objective selected"

        updated_by_id = {objective.id: objective for objective in objectives}
        if not selected:
            portfolio = portfolio.with_objectives(
                tuple(updated_by_id.values()),
                decision_history=_merge_text(
                    portfolio.decision_history,
                    bootstrap_entries + (reason,),
                    limit=20,
                ),
            )
            portfolio_ref = await self._persist_portfolio(portfolio)
            await self._backend.append_decision(mandate, reason)
            return ResidentPortfolioRun(
                mandate=mandate,
                portfolio_ref=portfolio_ref,
                portfolio=portfolio,
                discovered_objectives=discovered,
                selected_objectives=(),
                advanced_objectives=(),
                decision=decision,
                decision_reason=reason,
                budget=budget.snapshot(),
            )

        for objective in selected:
            budget_decision = budget.can_continue()
            if not budget_decision.allowed:
                decision = ResidentPortfolioDecisionKind.STOP
                reason = budget_decision.reason
                break

            if objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value:
                updated = objective.with_updates(
                    last_reviewed_at=datetime.now(UTC),
                    priority_rationale=objective.priority_rationale
                    or "operator input is required before this objective can advance",
                )
                updated_by_id[objective.id] = updated
                decision = ResidentPortfolioDecisionKind.ASK_OPERATOR
                reason = updated.pending_question or "objective needs operator input"
                break

            active = objective.with_updates(
                status=ResidentObjectiveStatus.ACTIVE.value,
                last_advanced_at=datetime.now(UTC),
            )
            selected_for_run.append(active)
            await self._backend.write_objective(active)
            wake_run = await self._wake_runtime.run(_objective_mandate(mandate, active))
            snapshot = budget.record_usage(wake_run.budget.usage)
            updated = await self._review_objective(active, wake_run)
            updated_by_id[objective.id] = updated
            advanced.append(updated)
            decision = (
                ResidentPortfolioDecisionKind.STOP
                if not budget.can_continue().allowed
                else ResidentPortfolioDecisionKind.CONTINUE
            )
            if decision == ResidentPortfolioDecisionKind.STOP:
                reason = budget.can_continue().reason
            else:
                reason = f"advanced objective {updated.id}; portfolio budget remains"
            if not budget.can_continue().allowed:
                reason = f"max turns reached: {snapshot.turns_used}"
                break

        decision_entry = _decision_entry(decision=decision, reason=reason, selected=selected)
        portfolio = portfolio.with_objectives(
            tuple(updated_by_id.values()),
            decision_history=_merge_text(
                portfolio.decision_history,
                bootstrap_entries + (decision_entry,),
                limit=_DECISION_HISTORY_LIMIT,
            ),
            domain_model_ref=_DOMAIN_MODEL_REF
            if evidence.domain_model is not None
            else portfolio.domain_model_ref,
            **(await _gather_portfolio_links(self._backend)),
        )
        portfolio_ref = await self._persist_portfolio(portfolio)
        await self._backend.append_decision(mandate, decision_entry)
        return ResidentPortfolioRun(
            mandate=mandate,
            portfolio_ref=portfolio_ref,
            portfolio=portfolio,
            discovered_objectives=discovered,
            selected_objectives=tuple(selected_for_run) or selected,
            advanced_objectives=tuple(advanced),
            decision=decision,
            decision_reason=reason,
            budget=budget.snapshot(),
        )

    async def _load_portfolio(self, mandate: str) -> ResidentPortfolio:
        stored = await self._backend.read_portfolio(mandate)
        objectives = tuple(await self._backend.list_objectives(mandate))
        if stored is None:
            return ResidentPortfolio(mandate=mandate, objectives=objectives)
        if objectives:
            return stored.with_objectives(merge_objectives(stored.objectives + objectives))
        return stored

    async def _gather_evidence(self, mandate: str) -> ResidentPortfolioEvidence:
        domain_model = None
        workstreams: tuple[ResidentWorkstream, ...] = ()
        wake_records: tuple[Any, ...] = ()
        if self._expert_memory is not None:
            domain_model = await self._expert_memory.read_domain_model(mandate)
            if domain_model is not None:
                workstreams = tuple(await self._expert_memory.list_workstreams(_DOMAIN_MODEL_REF))
        if self._wake_memory is not None:
            wake_records = tuple(await self._wake_memory.list_wake_records(mandate, limit=10))
        return ResidentPortfolioEvidence(
            domain_model=domain_model,
            workstreams=workstreams,
            wake_records=wake_records,
            artifact_refs=tuple(await self._backend.list_refs("resident/domain-expert/artifacts")),
            consolidation_refs=tuple(
                await self._backend.list_refs("resident/domain-expert/consolidations")
            ),
        )

    async def _review_objective(
        self,
        objective: ResidentObjective,
        wake_run: WakefulResidentRun,
    ) -> ResidentObjective:
        updated = await _review_objective_against_wake(self._backend, objective, wake_run)
        await self._backend.write_objective(updated)
        return updated

    async def _persist_portfolio(self, portfolio: ResidentPortfolio) -> str:
        for objective in portfolio.objectives:
            await self._backend.write_objective(objective)
        return await self._backend.write_portfolio(portfolio)


class ResidentPortfolioValidator:
    """Read-only validator and dry-run selector for resident portfolios."""

    def __init__(
        self,
        *,
        backend: ResidentWorkItemBackend,
        max_selected: int = 1,
        max_active: int = 3,
    ) -> None:
        self._backend = backend
        self._max_selected = max_selected
        self._max_active = max_active

    async def validate(self, mandate: str) -> ResidentPortfolioValidationReport:
        portfolio = await self._backend.read_portfolio(mandate)
        objectives = tuple(await self._backend.list_objectives(mandate))
        issues: list[ResidentPortfolioValidationFinding] = []
        warnings: list[ResidentPortfolioValidationFinding] = []
        if portfolio is None:
            issues.append(
                _finding(
                    "missing_portfolio",
                    "Resident portfolio is missing.",
                )
            )
            portfolio = ResidentPortfolio(mandate=mandate, objectives=objectives)
        elif objectives:
            portfolio = portfolio.with_objectives(objectives)

        objectives_by_id: dict[str, list[ResidentObjective]] = {}
        for objective in objectives:
            objectives_by_id.setdefault(objective.id, []).append(objective)
            _validate_required_fields(objective, issues)
            _validate_status(objective, issues)
            _validate_stable_id(objective, warnings)
            _validate_objective_state(objective, issues, warnings)

        for objective_id, matches in objectives_by_id.items():
            if len(matches) > 1:
                issues.append(
                    _finding(
                        "duplicate_objective_id",
                        f"Objective id appears {len(matches)} times.",
                        objective_id=objective_id,
                    )
                )

        known_ids = set(objectives_by_id)
        for objective in objectives:
            for dependency in objective.dependencies:
                if dependency not in known_ids:
                    issues.append(
                        _finding(
                            "missing_dependency",
                            f"Dependency does not refer to a known objective: {dependency}",
                            objective_id=objective.id,
                        )
                    )

        _validate_portfolio_links(portfolio, warnings)
        prioritized = prioritize_objectives(objectives, mandate=mandate)
        dry_run_candidates = tuple(
            objective
            for objective in prioritized
            if objective.status != ResidentObjectiveStatus.NEEDS_OPERATOR.value
        )
        selected = select_objectives(
            dry_run_candidates,
            max_selected=self._max_selected,
            max_active=self._max_active,
        )
        completed_ids = {
            item.id for item in objectives if item.status == ResidentObjectiveStatus.COMPLETED.value
        }
        previews = tuple(
            _preview_objective(item, objectives, completed_ids=completed_ids)
            for item in prioritized
        )
        selected_preview = (
            _preview_objective(selected[0], objectives, completed_ids=completed_ids)
            if selected
            else None
        )
        eligible_ids = {
            item.id
            for item in select_objectives(
                dry_run_candidates,
                max_selected=999,
                max_active=999,
            )
        }
        blocked = tuple(
            preview
            for preview in previews
            if _is_blocked_objective(
                _objective_by_id(objectives, preview.objective_id),
                completed_ids,
            )
        )
        operator_needed = tuple(
            preview
            for preview in previews
            if _objective_by_id(objectives, preview.objective_id).status
            == ResidentObjectiveStatus.NEEDS_OPERATOR.value
        )
        eligible = tuple(preview for preview in previews if preview.objective_id in eligible_ids)
        selected_ids = {item.id for item in selected}
        skipped = tuple(
            _dry_run_non_selection_reason(
                objective,
                known_ids=known_ids,
                completed_ids=completed_ids,
                eligible_ids=eligible_ids,
                selected_ids=selected_ids,
            )
            for objective in prioritized
            if objective.id not in selected_ids
        )
        issue_tuple = tuple(issues)
        warning_tuple = tuple(warnings)
        verdict = "invalid" if issue_tuple else "warning" if warning_tuple else "valid"
        return ResidentPortfolioValidationReport(
            verdict=verdict,
            issues=issue_tuple,
            warnings=warning_tuple,
            selected_objective=selected_preview,
            eligible_objectives=eligible,
            blocked_objectives=blocked,
            operator_needed_objectives=operator_needed,
            skipped_reasons=tuple(reason for reason in skipped if reason),
            objective_counts_by_status=_counts_by_status(objectives),
            dependency_graph_summary=_dependency_summary(objectives),
            stale_duplicate_superseded_hints=_hints(objectives, issues, warnings),
            suggested_safe_next_action=_suggested_action(verdict, selected_preview),
            mutated_state=False,
        )


class ResidentPortfolioStewardRuntime:
    """Maintains and advances a resident portfolio over bounded steward passes."""

    def __init__(
        self,
        *,
        backend: ResidentWorkItemBackend,
        wake_runtime: WakefulRuntimePort,
        config: ResidentPortfolioStewardConfig | None = None,
    ) -> None:
        self._backend = backend
        self._wake_runtime = wake_runtime
        self._config = config or ResidentPortfolioStewardConfig()

    async def run(self, mandate: str) -> ResidentPortfolioStewardRun:
        passes: list[ResidentPortfolioStewardPassReport] = []
        advancements = 0
        max_passes = max(0, self._config.max_passes)
        if max_passes == 0:
            return ResidentPortfolioStewardRun(
                mandate=mandate,
                passes=(),
                final_action=ResidentPortfolioStewardActionKind.STOP,
                final_suggested_next_action="No stewardship passes allowed by config.",
            )

        for pass_number in range(1, max_passes + 1):
            report = await self._run_pass(
                mandate,
                pass_number=pass_number,
                advancements_used=advancements,
            )
            passes.append(report)
            if report.action_taken == ResidentPortfolioStewardActionKind.ADVANCE:
                advancements += 1
                continue
            if report.action_taken == ResidentPortfolioStewardActionKind.REPAIR:
                continue
            if report.action_taken in {
                ResidentPortfolioStewardActionKind.ASK_OPERATOR,
                ResidentPortfolioStewardActionKind.STOP,
                ResidentPortfolioStewardActionKind.SLEEP,
            }:
                break

        if len(passes) >= max_passes and passes[-1].action_taken in {
            ResidentPortfolioStewardActionKind.ADVANCE,
            ResidentPortfolioStewardActionKind.REPAIR,
        }:
            final_action = ResidentPortfolioStewardActionKind.STOP
            final_suggestion = f"Stopped after configured steward pass limit: {max_passes}."
        else:
            final_action = passes[-1].action_taken
            final_suggestion = passes[-1].final_suggested_next_action
        return ResidentPortfolioStewardRun(
            mandate=mandate,
            passes=tuple(passes),
            final_action=final_action,
            final_suggested_next_action=final_suggestion,
            budget=_sum_budget(tuple(item.budget for item in passes)),
        )

    async def _run_pass(
        self,
        mandate: str,
        *,
        pass_number: int,
        advancements_used: int,
    ) -> ResidentPortfolioStewardPassReport:
        validator = ResidentPortfolioValidator(
            backend=self._backend,
            max_selected=self._config.max_objectives_selected,
            max_active=self._config.max_active_objectives,
        )
        validation_before = await validator.validate(mandate)
        repairs_attempted: list[ResidentPortfolioRepairRecord] = []
        repairs_skipped: list[ResidentPortfolioRepairRecord] = []
        persisted_refs: list[str] = []

        if self._config.repair_enabled:
            repair_result = await self._repair_safe_drift(
                mandate,
                validation_before,
            )
            repairs_attempted.extend(repair_result[0])
            repairs_skipped.extend(repair_result[1])
            persisted_refs.extend(item.ref for item in repair_result[0] if item.ref)

        if repairs_attempted:
            decision_ref = await self._backend.append_decision(
                mandate,
                _steward_decision_entry(
                    pass_number,
                    ResidentPortfolioStewardActionKind.REPAIR,
                    f"repaired {len(repairs_attempted)} portfolio metadata issue(s)",
                ),
            )
            persisted_refs.append(decision_ref)
            validation_after = await validator.validate(mandate)
            return ResidentPortfolioStewardPassReport(
                pass_number=pass_number,
                validation_before=validation_before,
                validation_after=validation_after,
                repairs_attempted=tuple(repairs_attempted),
                repairs_skipped=tuple(repairs_skipped),
                selected_objective=validation_after.selected_objective,
                action_taken=ResidentPortfolioStewardActionKind.REPAIR,
                persisted_refs=tuple(persisted_refs),
                final_suggested_next_action=validation_after.suggested_safe_next_action,
            )

        selected = validation_before.selected_objective
        if selected is None:
            operator_questions = tuple(
                item.title for item in validation_before.operator_needed_objectives
            )
            action = (
                ResidentPortfolioStewardActionKind.ASK_OPERATOR
                if operator_questions
                else ResidentPortfolioStewardActionKind.SLEEP
            )
            reason = (
                f"operator input needed for: {operator_questions[0]}"
                if operator_questions
                else "no eligible objective selected"
            )
            decision_ref = await self._backend.append_decision(
                mandate,
                _steward_decision_entry(pass_number, action, reason),
            )
            validation_after = await validator.validate(mandate)
            return ResidentPortfolioStewardPassReport(
                pass_number=pass_number,
                validation_before=validation_before,
                validation_after=validation_after,
                repairs_attempted=tuple(repairs_attempted),
                repairs_skipped=tuple(repairs_skipped),
                selected_objective=None,
                action_taken=action,
                operator_questions=operator_questions,
                persisted_refs=(decision_ref,),
                final_suggested_next_action=reason,
            )

        if advancements_used >= self._config.max_advancements:
            reason = (
                f"selected {selected.objective_id} but advancement limit "
                f"{self._config.max_advancements} is reached"
            )
            decision_ref = await self._backend.append_decision(
                mandate,
                _steward_decision_entry(
                    pass_number,
                    ResidentPortfolioStewardActionKind.SLEEP,
                    reason,
                ),
            )
            validation_after = await validator.validate(mandate)
            return ResidentPortfolioStewardPassReport(
                pass_number=pass_number,
                validation_before=validation_before,
                validation_after=validation_after,
                repairs_attempted=tuple(repairs_attempted),
                repairs_skipped=tuple(repairs_skipped),
                selected_objective=selected,
                action_taken=ResidentPortfolioStewardActionKind.SLEEP,
                persisted_refs=(decision_ref,),
                final_suggested_next_action=reason,
            )

        return await self._advance_selected(
            mandate,
            pass_number=pass_number,
            validation_before=validation_before,
            repairs_skipped=tuple(repairs_skipped),
        )

    async def _repair_safe_drift(
        self,
        mandate: str,
        validation: ResidentPortfolioValidationReport,
    ) -> tuple[
        tuple[ResidentPortfolioRepairRecord, ...],
        tuple[ResidentPortfolioRepairRecord, ...],
    ]:
        listed = await self._backend.list_objectives(mandate)
        objectives = {objective.id: objective for objective in listed}
        attempted: list[ResidentPortfolioRepairRecord] = []
        skipped: list[ResidentPortfolioRepairRecord] = []

        for finding in tuple(validation.issues) + tuple(validation.warnings):
            if len(attempted) >= self._config.max_repairs_per_pass:
                skipped.append(
                    ResidentPortfolioRepairRecord(
                        code=finding.code,
                        objective_id=finding.objective_id,
                        action="skip",
                        reason="repair pass budget exhausted",
                    )
                )
                continue
            objective = objectives.get(finding.objective_id)
            if objective is None:
                skipped.append(_skipped_repair(finding, "no matching objective loaded"))
                continue

            repaired = self._repair_finding(finding, objective)
            if repaired is None:
                skipped.append(_skipped_repair(finding, _repair_skip_reason(finding)))
                continue

            updated, record = repaired
            ref = await self._backend.write_objective(updated)
            record = ResidentPortfolioRepairRecord(
                code=record.code,
                objective_id=record.objective_id,
                action=record.action,
                reason=record.reason,
                before=record.before,
                after=record.after,
                ref=ref,
            )
            attempted.append(record)
            objectives[updated.id] = updated

        if attempted:
            await self._persist_portfolio_with_objectives(
                mandate,
                tuple(objectives.values()),
                decision_entry=f"steward repaired {len(attempted)} portfolio issue(s)",
            )
        return tuple(attempted), tuple(skipped)

    def _repair_finding(
        self,
        finding: ResidentPortfolioValidationFinding,
        objective: ResidentObjective,
    ) -> tuple[ResidentObjective, ResidentPortfolioRepairRecord] | None:
        if finding.code == "resume_reason_missing" and _has_meaningful_items(
            objective.source_evidence
        ):
            reason = (
                f"Resume because stored evidence remains relevant: {objective.source_evidence[0]}"
            )
            return (
                objective.with_updates(reasoning=reason),
                ResidentPortfolioRepairRecord(
                    code=finding.code,
                    objective_id=objective.id,
                    action="repair",
                    reason="derived resume rationale from source evidence",
                    before=objective.reasoning,
                    after=reason,
                ),
            )
        if finding.code == "audit_context_missing" and _has_meaningful_items(
            objective.source_evidence
        ):
            reason = (
                "Terminal decision retained because source evidence says: "
                f"{objective.source_evidence[0]}"
            )
            return (
                objective.with_updates(reasoning=reason),
                ResidentPortfolioRepairRecord(
                    code=finding.code,
                    objective_id=objective.id,
                    action="repair",
                    reason="derived terminal audit context from source evidence",
                    before=objective.reasoning,
                    after=reason,
                ),
            )
        if finding.code == "operator_question_missing":
            question = f"What operator judgment is needed before continuing '{objective.title}'?"
            return (
                objective.with_updates(
                    pending_question=question,
                    reasoning=objective.reasoning
                    or "Steward needs human judgment before this objective can advance.",
                ),
                ResidentPortfolioRepairRecord(
                    code=finding.code,
                    objective_id=objective.id,
                    action="repair",
                    reason="kept objective operator-gated and added a concrete question",
                    before=objective.pending_question,
                    after=question,
                ),
            )
        if finding.code == "missing_dependency":
            reason = f"Blocked because a dependency is not known: {finding.message}"
            return (
                objective.with_updates(
                    status=ResidentObjectiveStatus.BLOCKED.value,
                    reasoning=reason,
                ),
                ResidentPortfolioRepairRecord(
                    code=finding.code,
                    objective_id=objective.id,
                    action="repair",
                    reason="marked objective blocked until dependency is represented",
                    before=objective.status,
                    after=ResidentObjectiveStatus.BLOCKED.value,
                ),
            )
        return None

    async def _advance_selected(
        self,
        mandate: str,
        *,
        pass_number: int,
        validation_before: ResidentPortfolioValidationReport,
        repairs_skipped: tuple[ResidentPortfolioRepairRecord, ...],
    ) -> ResidentPortfolioStewardPassReport:
        selected = validation_before.selected_objective
        if selected is None:
            raise ValueError("cannot advance without a selected objective")

        objectives = tuple(await self._backend.list_objectives(mandate))
        objective = _objective_by_id(objectives, selected.objective_id)
        active = objective.with_updates(
            status=ResidentObjectiveStatus.ACTIVE.value,
            last_advanced_at=datetime.now(UTC),
        )
        persisted_refs = [await self._backend.write_objective(active)]
        wake_run = await self._wake_runtime.run(_objective_mandate(mandate, active))
        updated = await _review_objective_against_wake(self._backend, active, wake_run)
        persisted_refs.append(await self._backend.write_objective(updated))
        follow_ups = self._follow_up_objectives(
            mandate,
            updated,
            wake_run,
            existing_objectives=objectives,
        )
        for follow_up in follow_ups:
            persisted_refs.append(await self._backend.write_objective(follow_up))

        decision_entry = _steward_decision_entry(
            pass_number,
            ResidentPortfolioStewardActionKind.ADVANCE,
            f"advanced {updated.id}; created {len(follow_ups)} follow-up objective(s)",
        )
        portfolio_ref = await self._persist_portfolio_with_objectives(
            mandate,
            tuple(
                item
                for item in objectives
                if item.id not in {updated.id, *(follow_up.id for follow_up in follow_ups)}
            )
            + (updated,)
            + follow_ups,
            decision_entry=decision_entry,
        )
        persisted_refs.append(portfolio_ref)
        persisted_refs.append(await self._backend.append_decision(mandate, decision_entry))
        validation_after = await ResidentPortfolioValidator(
            backend=self._backend,
            max_selected=self._config.max_objectives_selected,
            max_active=self._config.max_active_objectives,
        ).validate(mandate)
        operator_questions = tuple(
            item.pending_question for item in follow_ups if item.pending_question
        )
        return ResidentPortfolioStewardPassReport(
            pass_number=pass_number,
            validation_before=validation_before,
            validation_after=validation_after,
            repairs_attempted=(),
            repairs_skipped=repairs_skipped,
            selected_objective=selected,
            action_taken=ResidentPortfolioStewardActionKind.ADVANCE,
            advanced_objective_id=updated.id,
            new_follow_up_objectives=follow_ups,
            operator_questions=operator_questions,
            persisted_refs=tuple(persisted_refs),
            budget=wake_run.budget,
            final_suggested_next_action=validation_after.suggested_safe_next_action,
        )

    def _follow_up_objectives(
        self,
        mandate: str,
        objective: ResidentObjective,
        wake_run: WakefulResidentRun,
        *,
        existing_objectives: tuple[ResidentObjective, ...],
    ) -> tuple[ResidentObjective, ...]:
        existing_ids = {item.id for item in existing_objectives}
        follow_ups: list[ResidentObjective] = []
        if objective.status != ResidentObjectiveStatus.COMPLETED.value:
            follow_ups.append(
                _objective_from_text(
                    mandate,
                    title=f"Resolve proof gap: {objective.title}",
                    source=objective.expected_outcome,
                    kind=ResidentObjectiveKind.VERIFICATION,
                    reasoning="Advanced work did not yet satisfy objective proof criteria.",
                    proof="Proof gap is resolved, retired, or converted into a clearer objective.",
                    dependencies=(objective.id,),
                )
            )

        for cycle in wake_run.cycles:
            for finding in cycle.finding_summaries:
                follow_up = _follow_up_from_finding(
                    mandate,
                    objective,
                    finding,
                )
                if follow_up is not None:
                    follow_ups.append(follow_up)
            if cycle.decision == WakefulResidentDecisionKind.ASK_OPERATOR:
                question = cycle.decision_reason or f"How should '{objective.title}' proceed?"
                follow_ups.append(
                    _objective_from_text(
                        mandate,
                        title=f"Ask operator: {question}",
                        source=question,
                        kind=ResidentObjectiveKind.OPERATOR_QUESTION,
                        reasoning="Wakeful runtime requires human judgment before continuing.",
                        proof="Operator answer is recorded and converted into policy or next work.",
                        status=ResidentObjectiveStatus.NEEDS_OPERATOR,
                        dependencies=(objective.id,),
                        pending_question=question,
                    )
                )

        unique: list[ResidentObjective] = []
        seen = set(existing_ids)
        for follow_up in follow_ups:
            if follow_up.id in seen:
                continue
            seen.add(follow_up.id)
            unique.append(follow_up)
            if len(unique) >= self._config.max_follow_up_objectives:
                break
        return tuple(unique)

    async def _persist_portfolio_with_objectives(
        self,
        mandate: str,
        objectives: tuple[ResidentObjective, ...],
        *,
        decision_entry: str,
    ) -> str:
        portfolio, merged = await _merge_and_persist_objectives(self._backend, mandate, objectives)
        portfolio = portfolio.with_objectives(
            merged,
            decision_history=_merge_text(
                portfolio.decision_history, (decision_entry,), limit=_DECISION_HISTORY_LIMIT
            ),
            **(await _gather_portfolio_links(self._backend)),
        )
        return await self._backend.write_portfolio(portfolio)


class ResidentCapabilityDiscoveryRuntime:
    """Discovers ways to close resident capability gaps from portfolio state."""

    def __init__(
        self,
        *,
        backend: ResidentWorkItemBackend,
        discovery: CapabilityDiscoveryPort,
        expert_memory: ResidentDomainExpertMemoryPort | None = None,
        config: ResidentCapabilityDiscoveryConfig | None = None,
    ) -> None:
        self._backend = backend
        self._discovery = discovery
        self._expert_memory = expert_memory
        self._config = config or ResidentCapabilityDiscoveryConfig()

    async def run(self, mandate: str) -> ResidentCapabilityDiscoveryReport:
        objectives = tuple(await self._backend.list_objectives(mandate))
        gaps = detect_capability_gaps(objectives)
        if not gaps:
            return ResidentCapabilityDiscoveryReport(
                mandate=mandate,
                selected_gap=None,
                discovery_result=None,
                created_or_updated_objectives=(),
                operator_questions=(),
                persisted_refs=(),
                budget_notes="no capability gap selected",
                final_suggested_next_action="No capability gap needs discovery.",
            )

        gap = gaps[0]
        result = await self._discovery.discover(mandate, gap)
        result = _limit_discovery_options(result, self._config.max_options)
        discovery_ref = await self._backend.write_capability_discovery(
            result.gap.id,
            render_capability_discovery_result(result),
        )
        follow_ups = _objectives_from_capability_discovery(
            result,
            limit=self._config.max_follow_up_objectives,
        )
        persisted_refs = [discovery_ref]
        for objective in follow_ups:
            persisted_refs.append(await self._backend.write_objective(objective))

        updated_objectives = follow_ups
        source = _objective_by_id_or_none(objectives, gap.source_objective_id)
        if source is not None:
            updated = source.with_updates(
                artifact_links=_merge_text(source.artifact_links, (discovery_ref,)),
                proof_progress=_merge_text(
                    source.proof_progress,
                    (f"capability discovery persisted: {discovery_ref}",),
                ),
                reasoning=source.reasoning
                or f"Capability discovery selected for gap: {gap.capability}",
            )
            persisted_refs.append(await self._backend.write_objective(updated))
            updated_objectives = (updated,) + follow_ups

        portfolio_ref = await self._persist_portfolio(
            mandate,
            tuple(item for item in objectives if item.id != gap.source_objective_id)
            + updated_objectives,
            decision_entry=(
                f"capability discovery completed for {gap.capability}; "
                f"created {len(follow_ups)} follow-up objective(s)"
            ),
        )
        persisted_refs.append(portfolio_ref)
        decision_ref = await self._backend.append_decision(
            mandate,
            f"{datetime.now(UTC).isoformat()} [capability_discovery] {gap.capability}",
        )
        persisted_refs.append(decision_ref)
        persisted_refs.extend(
            await self._consolidate_capability_learning(
                mandate,
                result=result,
                discovery_ref=discovery_ref,
                follow_ups=follow_ups,
            )
        )
        operator_questions = tuple(
            objective.pending_question for objective in follow_ups if objective.pending_question
        )
        return ResidentCapabilityDiscoveryReport(
            mandate=mandate,
            selected_gap=gap,
            discovery_result=result,
            created_or_updated_objectives=updated_objectives,
            operator_questions=operator_questions,
            persisted_refs=tuple(persisted_refs),
            budget_notes=result.budget_notes,
            final_suggested_next_action=(
                f"Run safe experiment: {result.recommended_safe_next_experiment}"
            ),
        )

    async def _consolidate_capability_learning(
        self,
        mandate: str,
        *,
        result: ResidentCapabilityDiscoveryResult,
        discovery_ref: str,
        follow_ups: tuple[ResidentObjective, ...],
    ) -> tuple[str, ...]:
        return await _consolidate_learning(
            self._expert_memory,
            mandate,
            lambda model: _domain_model_with_capability_learning(
                model,
                result=result,
                discovery_ref=discovery_ref,
                follow_ups=follow_ups,
            ),
        )

    async def _persist_portfolio(
        self,
        mandate: str,
        objectives: tuple[ResidentObjective, ...],
        *,
        decision_entry: str,
    ) -> str:
        portfolio, merged = await _merge_and_persist_objectives(self._backend, mandate, objectives)
        portfolio = portfolio.with_objectives(
            merged,
            decision_history=_merge_text(
                portfolio.decision_history, (decision_entry,), limit=_DECISION_HISTORY_LIMIT
            ),
            artifact_links=_merge_text(
                portfolio.artifact_links,
                tuple(await self._backend.list_refs(_CAPABILITY_DISCOVERY_PREFIX)),
            ),
        )
        return await self._backend.write_portfolio(portfolio)


class ResidentDelegationRuntime:
    """Orchestrates bounded delegated execution from resident portfolio objectives."""

    def __init__(
        self,
        *,
        backend: ResidentWorkItemBackend,
        executor: ResidentExecutionPort,
        expert_memory: ResidentDomainExpertMemoryPort | None = None,
        config: ResidentDelegationConfig | None = None,
    ) -> None:
        self._backend = backend
        self._executor = executor
        self._expert_memory = expert_memory
        self._config = config or ResidentDelegationConfig()

    async def run(self, mandate: str) -> ResidentDelegationReport:
        objectives = tuple(await self._backend.list_objectives(mandate))
        delegations = tuple(await self._backend.list_delegations(mandate))
        persisted_refs: list[str] = []
        updated_objectives: list[ResidentObjective] = []
        objective_updates: dict[str, ResidentObjective] = {item.id: item for item in objectives}
        duplicate_reconciled_count = 0

        if self._config.reconcile_duplicate_delegations:
            duplicate_reconciled = await self._reconcile_duplicate_active_delegations(
                objectives=objectives,
                delegations=delegations,
                objective_updates=objective_updates,
                persisted_refs=persisted_refs,
            )
            if duplicate_reconciled:
                duplicate_reconciled_count = len(duplicate_reconciled)
                delegations = tuple(await self._backend.list_delegations(mandate))
                updated_objectives.extend(duplicate_reconciled)

        abandoned = await self._abandon_stale_delegations(
            objectives=objectives,
            delegations=delegations,
            objective_updates=objective_updates,
            persisted_refs=persisted_refs,
        )
        if abandoned:
            delegations = tuple(await self._backend.list_delegations(mandate))
            updated_objectives.extend(abandoned)

        selection_objectives = tuple(objective_updates.values())
        active_delegation_count = _active_unresolved_delegation_count(delegations)
        launch_capacity = max(0, self._config.max_delegations - active_delegation_count)
        selected, gated = select_delegation_candidates(
            selection_objectives,
            delegations=delegations,
            mandate=mandate,
            max_selected=launch_capacity,
            approved_risk_objective_ids=self._config.approved_risk_objective_ids,
        )

        created_delegations: list[ResidentDelegationRecord] = []
        follow_ups: list[ResidentObjective] = []
        operator_questions: list[str] = []
        reviews: list[ResidentDelegationReview] = []
        pending_operator_questions = {
            objective.pending_question
            for objective in selection_objectives
            if objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value
            if objective.pending_question
        }

        for objective in gated:
            question = _delegation_operator_question(objective)
            if question in pending_operator_questions:
                continue
            operator_objective = _delegation_operator_objective(
                mandate,
                objective,
                question=question,
            )
            pending_operator_questions.add(question)
            operator_questions.append(question)
            follow_ups.append(operator_objective)
            objective_updates[operator_objective.id] = operator_objective
            persisted_refs.append(await self._backend.write_objective(operator_objective))

        for objective in selected:
            brief = build_worker_brief(mandate, objective)
            session = await self._executor.launch(brief)
            record = ResidentDelegationRecord(
                id=_delegation_id(objective),
                source_objective_id=objective.id,
                backend_session_id=session.session_id,
                backend_name=session.backend_name,
                brief=brief,
                status=session.status,
                reason=_delegation_reason(objective),
                risk_boundaries=objective.risk_boundaries,
            )
            created_delegations.append(record)
            persisted_refs.append(await self._backend.write_delegation(record))
            advanced = objective.with_updates(
                status=ResidentObjectiveStatus.ACTIVE.value,
                proof_progress=_merge_text(
                    objective.proof_progress,
                    (f"delegated to {session.backend_name}:{session.session_id}",),
                ),
                last_advanced_at=datetime.now(UTC),
            )
            updated_objectives.append(advanced)
            objective_updates[objective.id] = advanced
            persisted_refs.append(await self._backend.write_objective(advanced))

        observed_results: list[ResidentExecutionResult] = []
        observable = tuple(created_delegations) + tuple(
            item
            for item in delegations
            if not item.result_refs
            if item.status
            in {
                ResidentDelegationStatus.LAUNCHED.value,
                ResidentDelegationStatus.RUNNING.value,
                ResidentDelegationStatus.COMPLETED.value,
            }
        )
        seen_delegations: set[str] = set()
        for record in observable:
            if len(observed_results) >= self._config.max_observations:
                break
            if record.id in seen_delegations:
                continue
            seen_delegations.add(record.id)
            status = await self._executor.read_status(record.backend_session_id)
            result = await self._executor.read_result(record.backend_session_id)
            if result is None:
                updated_record = record.with_updates(status=status.status)
                persisted_refs.append(await self._backend.write_delegation(updated_record))
                continue
            if result.status not in _TERMINAL_DELEGATION_RESULT_STATUSES:
                updated_record = record.with_updates(status=result.status or status.status)
                persisted_refs.append(await self._backend.write_delegation(updated_record))
                continue
            observed_results.append(result)
            result_ref = await self._backend.write_delegation_result(
                record.id,
                result,
                render_delegation_result(record, result),
            )
            persisted_refs.append(result_ref)
            source = objective_updates.get(record.source_objective_id) or _objective_by_id_or_none(
                objectives,
                record.source_objective_id,
            )
            review = review_delegation_result(source, record, result)
            reviews.append(review)
            operator_questions.extend(
                question
                for question in review.operator_questions
                if question and question not in pending_operator_questions
            )
            pending_operator_questions.update(review.operator_questions)
            review_ref = await self._backend.write_delegation_review(
                review,
                render_delegation_review(review),
            )
            persisted_refs.append(review_ref)
            created_from_result = _follow_ups_from_delegation_result(
                mandate,
                source,
                result,
                review,
                limit=max(
                    0,
                    self._config.max_follow_up_objectives - len(follow_ups),
                ),
                max_retry_depth=max(0, self._config.max_retry_follow_up_depth),
            )
            for follow_up in created_from_result:
                follow_ups.append(follow_up)
                objective_updates[follow_up.id] = follow_up
                persisted_refs.append(await self._backend.write_objective(follow_up))
            consolidation_refs = await self._consolidate_delegation_learning(
                mandate,
                record=record,
                result=result,
                review=review,
                result_ref=result_ref,
                review_ref=review_ref,
                follow_ups=created_from_result,
            )
            persisted_refs.extend(consolidation_refs)
            if source is not None:
                absorbed = _absorb_delegation_result(
                    source,
                    result,
                    review,
                    result_ref,
                    consolidation_refs=tuple(consolidation_refs),
                )
                if (
                    review.decision != ResidentDelegationReviewDecision.COMPLETE.value
                    and created_from_result
                ):
                    absorbed = absorbed.with_updates(superseded_by=created_from_result[0].id)
                updated_objectives.append(absorbed)
                objective_updates[source.id] = absorbed
                persisted_refs.append(await self._backend.write_objective(absorbed))
            updated_record = record.with_updates(
                status=result.status,
                result_refs=_merge_text(record.result_refs, (result_ref,)),
                follow_up_objective_refs=_merge_text(
                    record.follow_up_objective_refs,
                    tuple(item.id for item in created_from_result),
                ),
            )
            persisted_refs.append(await self._backend.write_delegation(updated_record))

        portfolio_ref = await self._persist_portfolio(
            mandate,
            tuple(objective_updates.values()),
            decision_entry=(
                f"delegation pass launched {len(created_delegations)} session(s), "
                f"observed {len(observed_results)} result(s), "
                f"gated {len(gated)} objective(s), "
                f"reconciled {duplicate_reconciled_count} duplicate session(s)"
            ),
        )
        persisted_refs.append(portfolio_ref)
        decision_ref = await self._backend.append_decision(
            mandate,
            f"{datetime.now(UTC).isoformat()} [delegation] "
            f"launched={len(created_delegations)} "
            f"observed={len(observed_results)} gated={len(gated)} "
            f"duplicates_reconciled={duplicate_reconciled_count}",
        )
        persisted_refs.append(decision_ref)

        return ResidentDelegationReport(
            mandate=mandate,
            selected_objectives=selected,
            created_delegations=tuple(created_delegations),
            skipped_or_gated_objectives=gated,
            observed_results=tuple(observed_results),
            reviews=tuple(reviews),
            updated_objectives=tuple(updated_objectives),
            created_follow_up_objectives=tuple(follow_ups),
            operator_questions=tuple(operator_questions),
            persisted_refs=tuple(persisted_refs),
            final_suggested_next_action=_delegation_next_action(
                created_delegations,
                observed_results,
                operator_questions,
            ),
        )

    async def _consolidate_delegation_learning(
        self,
        mandate: str,
        *,
        record: ResidentDelegationRecord,
        result: ResidentExecutionResult,
        review: ResidentDelegationReview,
        result_ref: str,
        review_ref: str,
        follow_ups: tuple[ResidentObjective, ...],
    ) -> tuple[str, ...]:
        return await _consolidate_learning(
            self._expert_memory,
            mandate,
            lambda model: _domain_model_with_delegation_learning(
                model,
                record=record,
                result=result,
                review=review,
                result_ref=result_ref,
                review_ref=review_ref,
                follow_ups=follow_ups,
            ),
        )

    async def _reconcile_duplicate_active_delegations(
        self,
        *,
        objectives: tuple[ResidentObjective, ...],
        delegations: tuple[ResidentDelegationRecord, ...],
        objective_updates: dict[str, ResidentObjective],
        persisted_refs: list[str],
    ) -> list[ResidentObjective]:
        active_by_source: dict[str, list[ResidentDelegationRecord]] = {}
        for record in delegations:
            if not _active_unresolved_delegation(record):
                continue
            active_by_source.setdefault(record.source_objective_id, []).append(record)

        updated: list[ResidentObjective] = []
        now = datetime.now(UTC)
        for source_id, records in active_by_source.items():
            if len(records) <= 1:
                continue
            ordered = sorted(records, key=_delegation_keep_sort_key)
            keep = ordered[0]
            for duplicate in ordered[1:]:
                reason = (
                    "cancelling duplicate active delegated session for source objective "
                    f"{source_id}; keeping {keep.id}:{keep.backend_session_id}"
                )
                cancelled = await self._executor.cancel(duplicate.backend_session_id, reason)
                updated_record = duplicate.with_updates(
                    status=cancelled.status,
                    reason=_compact_line(f"{duplicate.reason}; {reason}", limit=240),
                )
                persisted_refs.append(await self._backend.write_delegation(updated_record))
                source = objective_updates.get(source_id) or _objective_by_id_or_none(
                    objectives,
                    source_id,
                )
                if source is None:
                    continue
                repaired = source.with_updates(
                    proof_progress=_merge_text(
                        source.proof_progress,
                        (
                            f"duplicate delegation {duplicate.id} cancelled: "
                            f"{cancelled.status}: {cancelled.summary or reason}",
                        ),
                    ),
                    last_reviewed_at=now,
                )
                objective_updates[source_id] = repaired
                updated.append(repaired)
                persisted_refs.append(await self._backend.write_objective(repaired))
        return updated

    async def _abandon_stale_delegations(
        self,
        *,
        objectives: tuple[ResidentObjective, ...],
        delegations: tuple[ResidentDelegationRecord, ...],
        objective_updates: dict[str, ResidentObjective],
        persisted_refs: list[str],
    ) -> list[ResidentObjective]:
        max_age = float(self._config.abandon_after_seconds)
        if max_age <= 0:
            return []
        now = datetime.now(UTC)
        updated: list[ResidentObjective] = []
        for record in delegations:
            if record.result_refs:
                continue
            if record.status not in {
                ResidentDelegationStatus.LAUNCHED.value,
                ResidentDelegationStatus.RUNNING.value,
            }:
                continue
            age = (now - record.updated_at).total_seconds()
            if age < max_age:
                continue
            reason = (
                f"abandoning stale delegated session after {int(age)}s without result refs"
            )
            cancelled = await self._executor.cancel(record.backend_session_id, reason)
            updated_record = record.with_updates(
                status=cancelled.status,
                reason=_compact_line(f"{record.reason}; {reason}", limit=240),
            )
            persisted_refs.append(await self._backend.write_delegation(updated_record))
            source = _objective_by_id_or_none(objectives, record.source_objective_id)
            if source is None:
                continue
            blocked = source.with_updates(
                status=ResidentObjectiveStatus.BLOCKED.value,
                proof_progress=_merge_text(
                    source.proof_progress,
                    (
                        f"delegation {record.id} abandoned: "
                        f"{cancelled.status}: {cancelled.summary or reason}",
                    ),
                ),
                last_reviewed_at=now,
            )
            objective_updates[source.id] = blocked
            updated.append(blocked)
            persisted_refs.append(await self._backend.write_objective(blocked))
        return updated

    async def _persist_portfolio(
        self,
        mandate: str,
        objectives: tuple[ResidentObjective, ...],
        *,
        decision_entry: str,
    ) -> str:
        portfolio, merged = await _merge_and_persist_objectives(self._backend, mandate, objectives)
        portfolio = portfolio.with_objectives(
            merged,
            decision_history=_merge_text(
                portfolio.decision_history, (decision_entry,), limit=_DECISION_HISTORY_LIMIT
            ),
            workstream_links=_merge_text(
                portfolio.workstream_links,
                tuple(await self._backend.list_refs(_DELEGATION_PREFIX)),
            ),
            artifact_links=_merge_text(
                portfolio.artifact_links,
                tuple(await self._backend.list_refs(_DELEGATION_RESULT_PREFIX)),
            ),
            consolidation_links=_merge_text(
                portfolio.consolidation_links,
                tuple(await self._backend.list_refs(_DELEGATION_REVIEW_PREFIX)),
            ),
        )
        return await self._backend.write_portfolio(portfolio)


class ResidentAutonomyLoopRuntime:
    """Runs bounded multi-cycle resident autonomy over portfolio and delegation."""

    def __init__(
        self,
        *,
        backend: ResidentWorkItemBackend,
        executor: ResidentExecutionPort,
        ask_operator: OperatorContactPort | None = None,
        wake_memory: WakefulResidentMemoryPort | None = None,
        expert_memory: ResidentDomainExpertMemoryPort | None = None,
        config: ResidentAutonomyLoopConfig | None = None,
    ) -> None:
        self._backend = backend
        self._executor = executor
        self._ask_operator = ask_operator
        self._wake_memory = wake_memory
        self._expert_memory = expert_memory
        self._config = config or ResidentAutonomyLoopConfig()

    async def run(self, mandate: str) -> ResidentAutonomyRun:
        started = datetime.now(UTC)
        cycles: list[ResidentAutonomyCycleReport] = []
        persisted_refs: list[str] = []
        operator_questions: list[str] = []
        operator_contacts: list[OperatorContactResult] = []
        stored_objectives = tuple(await self._backend.list_objectives(mandate))
        approved_risk_objective_ids = set(self._config.approved_risk_objective_ids)
        approved_risk_objective_ids.update(
            approved_risk_objective_ids_from_objectives(stored_objectives)
        )

        for cycle_number in range(1, max(0, self._config.max_cycles) + 1):
            if (datetime.now(UTC) - started).total_seconds() > self._config.max_wall_clock_seconds:
                break
            delegation = await ResidentDelegationRuntime(
                backend=self._backend,
                executor=self._executor,
                expert_memory=self._expert_memory,
                config=ResidentDelegationConfig(
                    max_delegations=self._config.max_delegations_per_cycle,
                    max_observations=self._config.max_observations_per_cycle,
                    max_follow_up_objectives=self._config.max_review_attempts,
                    max_retry_follow_up_depth=self._config.max_retry_follow_up_depth,
                    approved_risk_objective_ids=tuple(sorted(approved_risk_objective_ids)),
                    abandon_after_seconds=self._config.abandon_after_seconds,
                    reconcile_duplicate_delegations=self._config.reconcile_duplicate_delegations,
                ),
            ).run(mandate)
            operator_questions.extend(delegation.operator_questions)
            cycle_refs = list(delegation.persisted_refs)
            cycle_contacts = await self._contact_operator_for_delegation(mandate, delegation)
            for contact in cycle_contacts:
                contact_ref = await self._backend.write_operator_contact(contact)
                cycle_refs.append(contact_ref)
                if contact.status == OperatorContactStatus.ANSWERED.value:
                    decision_ref = await self._backend.append_decision(
                        mandate,
                        (
                            f"{datetime.now(UTC).isoformat()} [operator_contact] "
                            f"{contact.request.id} status={contact.status} "
                            f"approved={contact.approved}"
                        ),
                    )
                    cycle_refs.append(decision_ref)
                if contact.approved is True and contact.request.source_objective_id:
                    approved_risk_objective_ids.add(contact.request.source_objective_id)
            operator_contacts.extend(cycle_contacts)
            wake_ref = await self._write_autonomy_wake_record(
                mandate,
                cycle_number=cycle_number,
                delegation=delegation,
                operator_contacts=tuple(cycle_contacts),
            )
            if wake_ref:
                cycle_refs.append(wake_ref)
            cycle = ResidentAutonomyCycleReport(
                cycle_number=cycle_number,
                delegation_report=delegation,
                selected_objectives=delegation.selected_objectives,
                review_decisions=delegation.reviews,
                persisted_refs=tuple(cycle_refs),
                operator_questions=delegation.operator_questions,
                final_suggested_next_action=delegation.final_suggested_next_action,
                operator_contacts=tuple(cycle_contacts),
            )
            cycles.append(cycle)
            persisted_refs.extend(cycle_refs)
            if any(
                contact.status == OperatorContactStatus.PENDING.value
                for contact in cycle_contacts
            ):
                break
            if delegation.operator_questions and not any(
                contact.approved is True for contact in cycle_contacts
            ):
                break
            if not _delegation_report_had_activity(delegation):
                break
            if cycle_number < max(0, self._config.max_cycles):
                remaining = self._config.max_wall_clock_seconds - (
                    datetime.now(UTC) - started
                ).total_seconds()
                pause = min(max(0.0, self._config.sleep_between_cycles_seconds), remaining)
                if pause > 0:
                    await asyncio.sleep(pause)

        final = _autonomy_final_suggestion(cycles, operator_contacts)
        return ResidentAutonomyRun(
            mandate=mandate,
            cycles=tuple(cycles),
            persisted_refs=tuple(persisted_refs),
            operator_questions=tuple(operator_questions),
            final_suggested_next_action=final,
            operator_contacts=tuple(operator_contacts),
        )

    async def _contact_operator_for_delegation(
        self,
        mandate: str,
        delegation: ResidentDelegationReport,
    ) -> tuple[OperatorContactResult, ...]:
        if self._ask_operator is None or not delegation.operator_questions:
            return ()
        contacts: list[OperatorContactResult] = []
        for question in delegation.operator_questions:
            objective = _objective_for_delegation_question(delegation, question)
            request = _operator_contact_request(mandate, question, objective)
            try:
                result = await self._ask_operator.ask(request)
            except Exception as exc:
                result = OperatorContactResult(
                    request=request,
                    status=OperatorContactStatus.FAILED.value,
                    answer=str(exc),
                    approved=False,
                    responded_at=datetime.now(UTC),
                )
            contacts.append(result)
            if result.status == OperatorContactStatus.PENDING.value:
                break
        return tuple(contacts)

    async def _write_autonomy_wake_record(
        self,
        mandate: str,
        *,
        cycle_number: int,
        delegation: ResidentDelegationReport,
        operator_contacts: tuple[OperatorContactResult, ...] = (),
    ) -> str:
        if self._wake_memory is None:
            return ""
        pending_contacts = tuple(
            item
            for item in operator_contacts
            if item.status == OperatorContactStatus.PENDING.value
        )
        if pending_contacts:
            decision = WakefulResidentDecisionKind.ASK_OPERATOR
            reason = f"waiting for operator answer: {pending_contacts[0].request.question}"
        elif delegation.operator_questions:
            decision = WakefulResidentDecisionKind.ASK_OPERATOR
            reason = delegation.operator_questions[0]
        elif _delegation_report_had_activity(delegation):
            decision = WakefulResidentDecisionKind.CONTINUE
            reason = delegation.final_suggested_next_action
        else:
            decision = WakefulResidentDecisionKind.SLEEP
            reason = "no delegate-ready resident work"
        record = WakefulResidentCycleRecord(
            cycle_number=cycle_number,
            mandate=mandate,
            prior_domain_model_ref=_DOMAIN_MODEL_REF,
            attention_reason=_autonomy_attention_reason(delegation),
            selected_action="resident autonomy delegation pass",
            work_created_or_advanced=tuple(_delegation_cycle_work_items(delegation)),
            artifact_refs=tuple(delegation.persisted_refs),
            finding_summaries=tuple(_delegation_cycle_findings(delegation)),
            decision=decision,
            decision_reason=reason,
            budget=ResidentBudgetSnapshot(
                turns_used=1,
                usage=_result_usage(delegation.observed_results),
            ),
        )
        return await self._wake_memory.write_wake_record(record)
