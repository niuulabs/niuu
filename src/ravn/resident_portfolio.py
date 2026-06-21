"""Resident long-horizon work management V0."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ravn.domain.models import TokenUsage
from ravn.domain.operator_contact import (
    OperatorContactKind,
    OperatorContactPort,
    OperatorContactPurpose,
    OperatorContactRequest,
    OperatorContactResult,
    OperatorContactStatus,
)
from ravn.domain.resident_continuation import ResidentBudgetLimits, ResidentBudgetSnapshot
from ravn.domain.resident_expert import (
    ExpertArtifact,
    ResidentDomainExpertMemoryPort,
    ResidentDomainModel,
    ResidentWorkstream,
    ResidentWorkstreamStatus,
    WorkstreamExecutionResult,
)
from ravn.domain.resident_portfolio import (
    CapabilityDiscoveryPort,
    ResidentAutonomyCycleReport,
    ResidentAutonomyRun,
    ResidentCapabilityDiscoveryReport,
    ResidentCapabilityDiscoveryResult,
    ResidentCapabilityGap,
    ResidentCapabilityOption,
    ResidentDelegationRecord,
    ResidentDelegationReport,
    ResidentDelegationReview,
    ResidentDelegationReviewDecision,
    ResidentDelegationStatus,
    ResidentExecutionPort,
    ResidentExecutionResult,
    ResidentExecutionSession,
    ResidentObjective,
    ResidentObjectiveDryRunPreview,
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
    ResidentPortfolioValidationSeverity,
    ResidentWorkerBrief,
    ResidentWorkItemBackend,
)
from ravn.domain.wakeful_resident import (
    WakefulResidentCycleRecord,
    WakefulResidentDecisionKind,
    WakefulResidentMemoryPort,
    WakefulResidentRun,
)
from ravn.ports.capability import (
    WorkflowCapabilityPort,
    WorkflowLaunchRequest,
    WorkflowRunEvent,
    WorkflowRunReference,
    WorkflowRunStatus,
)
from ravn.ports.mimir import MimirPort
from ravn.resident_continuation import ResidentRunBudget, _compact_line, _slug
from ravn.resident_operator_contact import approved_risk_objective_ids_from_objectives

_PORTFOLIO_PATH = "resident/portfolio/portfolio.md"
_OBJECTIVE_PREFIX = "resident/portfolio/objectives"
_DECISION_PREFIX = "resident/portfolio/decisions"
_CAPABILITY_DISCOVERY_PREFIX = "resident/capability-discovery"
_DELEGATION_PREFIX = "resident/delegations"
_DELEGATION_RESULT_PREFIX = "resident/delegation-results"
_DELEGATION_REVIEW_PREFIX = "resident/delegation-reviews"
_OPERATOR_CONTACT_PREFIX = "resident/operator-contacts"
_DOMAIN_MODEL_REF = "resident/domain-expert/domain-model.md"


class WakefulRuntimePort(Protocol):
    """Boundary for advancing one selected objective through wakeful runtime."""

    async def run(self, mandate: str) -> WakefulResidentRun:
        """Advance bounded resident work for a mandate/objective."""


@dataclass(frozen=True)
class ResidentPortfolioConfig:
    """Bounds for one long-horizon portfolio management invocation."""

    max_objectives_selected: int = 1
    max_active_objectives: int = 3
    max_wake_cycles: int = 1
    max_workstream_turns: int = 1
    max_wall_clock_seconds: float = 1800.0
    max_tokens: int = 0
    bootstrap_when_empty: bool = True


@dataclass(frozen=True)
class ResidentPortfolioStewardConfig:
    """Bounds for resident portfolio stewardship."""

    max_passes: int = 3
    max_repairs_per_pass: int = 4
    max_follow_up_objectives: int = 3
    max_objectives_selected: int = 1
    max_active_objectives: int = 3
    max_advancements: int = 1
    repair_enabled: bool = True


@dataclass(frozen=True)
class ResidentCapabilityDiscoveryConfig:
    """Bounds for one resident capability discovery pass."""

    max_gaps: int = 1
    max_options: int = 4
    max_follow_up_objectives: int = 4


@dataclass(frozen=True)
class ResidentDelegationConfig:
    """Bounds for one resident delegation orchestration pass."""

    max_delegations: int = 2
    max_observations: int = 4
    max_follow_up_objectives: int = 4
    max_retry_follow_up_depth: int = 1
    approved_risk_objective_ids: tuple[str, ...] = ()
    abandon_after_seconds: float = 0.0
    reconcile_duplicate_delegations: bool = True


@dataclass(frozen=True)
class ResidentAutonomyLoopConfig:
    """Bounds for one resident autonomy loop invocation."""

    max_cycles: int = 2
    max_delegations_per_cycle: int = 1
    max_observations_per_cycle: int = 4
    max_review_attempts: int = 4
    max_retry_follow_up_depth: int = 1
    max_wall_clock_seconds: float = 1800.0
    sleep_between_cycles_seconds: float = 0.0
    approved_risk_objective_ids: tuple[str, ...] = ()
    abandon_after_seconds: float = 0.0
    reconcile_duplicate_delegations: bool = True


@dataclass(frozen=True)
class ResidentPortfolioEvidence:
    """Compact evidence used to discover/prioritize objectives."""

    domain_model: ResidentDomainModel | None = None
    workstreams: tuple[ResidentWorkstream, ...] = ()
    wake_records: tuple[Any, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    consolidation_refs: tuple[str, ...] = ()


class LocalResidentWorkItemBackend(ResidentWorkItemBackend):
    """Filesystem-backed resident work item backend."""

    # TODO: Add a Ting-backed ResidentWorkItemBackend adapter once Ting can use
    # Mimir as a lightweight ticket backend.

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    async def read_portfolio(self, mandate: str) -> ResidentPortfolio | None:
        path = self._root / _PORTFOLIO_PATH
        if not path.exists():
            return None
        portfolio = _parse_portfolio(path.read_text(encoding="utf-8"), mandate=mandate)
        objectives = tuple(await self.list_objectives(mandate))
        return portfolio.with_objectives(objectives) if objectives else portfolio

    async def write_portfolio(self, portfolio: ResidentPortfolio) -> str:
        return self._write(Path(_PORTFOLIO_PATH), _render_portfolio(portfolio))

    async def list_objectives(self, mandate: str) -> list[ResidentObjective]:
        base = self._root / _OBJECTIVE_PREFIX
        if not base.exists():
            return []
        objectives: list[ResidentObjective] = []
        for path in sorted(base.glob("*.md")):
            parsed = _parse_objective(path.read_text(encoding="utf-8"))
            if parsed is not None:
                objectives.append(parsed)
        return objectives

    async def write_objective(self, objective: ResidentObjective) -> str:
        rel = Path(_OBJECTIVE_PREFIX) / f"{objective.id}.md"
        return self._write(rel, _render_objective(objective))

    async def append_decision(self, mandate: str, entry: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        rel = Path(_DECISION_PREFIX) / f"{stamp}.md"
        return self._write(rel, f"# Resident Portfolio Decision\n\n{entry}\n")

    async def list_refs(self, prefix: str) -> list[str]:
        base = self._root / prefix
        if not base.exists():
            return []
        return sorted(str(path.relative_to(self._root)) for path in base.glob("*.md"))

    async def write_capability_discovery(self, discovery_id: str, content: str) -> str:
        rel = Path(_CAPABILITY_DISCOVERY_PREFIX) / f"{_slug(discovery_id)}.md"
        return self._write(rel, content)

    async def list_delegations(self, mandate: str) -> list[ResidentDelegationRecord]:
        base = self._root / _DELEGATION_PREFIX
        if not base.exists():
            return []
        delegations: list[ResidentDelegationRecord] = []
        for path in sorted(base.glob("*.md")):
            parsed = _parse_delegation(path.read_text(encoding="utf-8"))
            if parsed is not None:
                delegations.append(parsed)
        return delegations

    async def write_delegation(self, delegation: ResidentDelegationRecord) -> str:
        rel = Path(_DELEGATION_PREFIX) / f"{delegation.id}.md"
        return self._write(rel, _render_delegation(delegation))

    async def write_delegation_result(
        self,
        delegation_id: str,
        result: ResidentExecutionResult,
        content: str,
    ) -> str:
        filename = f"{_slug(delegation_id)}-{_slug(result.session_id)}.md"
        rel = Path(_DELEGATION_RESULT_PREFIX) / filename
        return self._write(rel, content)

    async def write_delegation_review(
        self,
        review: ResidentDelegationReview,
        content: str,
    ) -> str:
        rel = Path(_DELEGATION_REVIEW_PREFIX) / f"{review.id}.md"
        return self._write(rel, content)

    async def write_operator_contact(self, result: OperatorContactResult) -> str:
        rel = Path(_OPERATOR_CONTACT_PREFIX) / f"{result.request.id}.md"
        return self._write(rel, _render_operator_contact(result))

    def _write(self, rel: Path, content: str) -> str:
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(rel)


class MimirResidentWorkItemBackend(ResidentWorkItemBackend):
    """Mimir-backed resident work item backend."""

    # TODO: Add a Ting-backed ResidentWorkItemBackend adapter once Ting can use
    # Mimir as a lightweight ticket backend.

    def __init__(self, mimir: MimirPort) -> None:
        self._mimir = mimir

    async def read_portfolio(self, mandate: str) -> ResidentPortfolio | None:
        try:
            content = await self._mimir.read_page(_PORTFOLIO_PATH)
        except FileNotFoundError:
            return None
        portfolio = _parse_portfolio(content, mandate=mandate)
        objectives = tuple(await self.list_objectives(mandate))
        return portfolio.with_objectives(objectives) if objectives else portfolio

    async def write_portfolio(self, portfolio: ResidentPortfolio) -> str:
        await self._mimir.upsert_page(_PORTFOLIO_PATH, _render_portfolio(portfolio))
        return _PORTFOLIO_PATH

    async def list_objectives(self, mandate: str) -> list[ResidentObjective]:
        pages = await self._mimir.list_pages(prefix=_OBJECTIVE_PREFIX)
        objectives: list[ResidentObjective] = []
        for meta in sorted(pages, key=lambda page: getattr(page, "path", "")):
            try:
                content = await self._mimir.read_page(meta.path)
            except FileNotFoundError:
                continue
            parsed = _parse_objective(content)
            if parsed is not None:
                objectives.append(parsed)
        return objectives

    async def write_objective(self, objective: ResidentObjective) -> str:
        path = f"{_OBJECTIVE_PREFIX}/{objective.id}.md"
        await self._mimir.upsert_page(path, _render_objective(objective))
        return path

    async def append_decision(self, mandate: str, entry: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = f"{_DECISION_PREFIX}/{stamp}.md"
        await self._mimir.upsert_page(path, f"# Resident Portfolio Decision\n\n{entry}\n")
        return path

    async def list_refs(self, prefix: str) -> list[str]:
        pages = await self._mimir.list_pages(prefix=prefix)
        return sorted(getattr(page, "path", "") for page in pages if getattr(page, "path", ""))

    async def write_capability_discovery(self, discovery_id: str, content: str) -> str:
        path = f"{_CAPABILITY_DISCOVERY_PREFIX}/{_slug(discovery_id)}.md"
        await self._mimir.upsert_page(path, content)
        return path

    async def list_delegations(self, mandate: str) -> list[ResidentDelegationRecord]:
        pages = await self._mimir.list_pages(prefix=_DELEGATION_PREFIX)
        delegations: list[ResidentDelegationRecord] = []
        for meta in sorted(pages, key=lambda page: getattr(page, "path", "")):
            try:
                content = await self._mimir.read_page(meta.path)
            except FileNotFoundError:
                continue
            parsed = _parse_delegation(content)
            if parsed is not None:
                delegations.append(parsed)
        return delegations

    async def write_delegation(self, delegation: ResidentDelegationRecord) -> str:
        path = f"{_DELEGATION_PREFIX}/{delegation.id}.md"
        await self._mimir.upsert_page(path, _render_delegation(delegation))
        return path

    async def write_delegation_result(
        self,
        delegation_id: str,
        result: ResidentExecutionResult,
        content: str,
    ) -> str:
        filename = f"{_slug(delegation_id)}-{_slug(result.session_id)}.md"
        path = f"{_DELEGATION_RESULT_PREFIX}/{filename}"
        await self._mimir.upsert_page(path, content)
        return path

    async def write_delegation_review(
        self,
        review: ResidentDelegationReview,
        content: str,
    ) -> str:
        path = f"{_DELEGATION_REVIEW_PREFIX}/{review.id}.md"
        await self._mimir.upsert_page(path, content)
        return path

    async def write_operator_contact(self, result: OperatorContactResult) -> str:
        path = f"{_OPERATOR_CONTACT_PREFIX}/{result.request.id}.md"
        await self._mimir.upsert_page(path, _render_operator_contact(result))
        return path


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
                limit=20,
            ),
            domain_model_ref=_DOMAIN_MODEL_REF
            if evidence.domain_model is not None
            else portfolio.domain_model_ref,
            wake_record_links=tuple(await self._backend.list_refs("resident/wakeful/cycles")),
            workstream_links=tuple(
                await self._backend.list_refs("resident/domain-expert/workstreams")
            ),
            artifact_links=tuple(await self._backend.list_refs("resident/domain-expert/artifacts")),
            consolidation_links=tuple(
                await self._backend.list_refs("resident/domain-expert/consolidations")
            ),
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
        wake_links = tuple(await self._backend.list_refs("resident/wakeful/cycles"))
        artifact_links = _merge_text(
            tuple(await self._backend.list_refs("resident/domain-expert/artifacts")),
            tuple(ref for cycle in wake_run.cycles for ref in cycle.artifact_refs),
        )
        workstream_links = tuple(
            await self._backend.list_refs("resident/domain-expert/workstreams")
        )
        consolidation_links = tuple(
            await self._backend.list_refs("resident/domain-expert/consolidations")
        )
        proof_progress = _proof_progress_from_wake(wake_run)
        status = (
            ResidentObjectiveStatus.COMPLETED.value
            if _proof_satisfied(proof_progress, artifact_links, consolidation_links)
            else ResidentObjectiveStatus.PAUSED.value
        )
        updated = objective.with_updates(
            status=status,
            proof_progress=_merge_text(objective.proof_progress, proof_progress),
            artifact_links=_merge_text(objective.artifact_links, artifact_links),
            wake_links=_merge_text(objective.wake_links, wake_links),
            workstream_links=_merge_text(objective.workstream_links, workstream_links),
            consolidation_links=_merge_text(objective.consolidation_links, consolidation_links),
            last_reviewed_at=datetime.now(UTC),
        )
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
        updated = await self._review_steward_objective(active, wake_run)
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

    async def _review_steward_objective(
        self,
        objective: ResidentObjective,
        wake_run: WakefulResidentRun,
    ) -> ResidentObjective:
        wake_links = tuple(await self._backend.list_refs("resident/wakeful/cycles"))
        artifact_links = _merge_text(
            tuple(await self._backend.list_refs("resident/domain-expert/artifacts")),
            tuple(ref for cycle in wake_run.cycles for ref in cycle.artifact_refs),
        )
        workstream_links = tuple(
            await self._backend.list_refs("resident/domain-expert/workstreams")
        )
        consolidation_links = tuple(
            await self._backend.list_refs("resident/domain-expert/consolidations")
        )
        proof_progress = _proof_progress_from_wake(wake_run)
        status = (
            ResidentObjectiveStatus.COMPLETED.value
            if _proof_satisfied(proof_progress, artifact_links, consolidation_links)
            else ResidentObjectiveStatus.PAUSED.value
        )
        return objective.with_updates(
            status=status,
            proof_progress=_merge_text(objective.proof_progress, proof_progress),
            artifact_links=_merge_text(objective.artifact_links, artifact_links),
            wake_links=_merge_text(objective.wake_links, wake_links),
            workstream_links=_merge_text(objective.workstream_links, workstream_links),
            consolidation_links=_merge_text(objective.consolidation_links, consolidation_links),
            last_reviewed_at=datetime.now(UTC),
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
        stored = await self._backend.read_portfolio(mandate)
        portfolio = stored or ResidentPortfolio(mandate=mandate)
        merged = merge_objectives(objectives)
        for objective in merged:
            await self._backend.write_objective(objective)
        portfolio = portfolio.with_objectives(
            merged,
            decision_history=_merge_text(portfolio.decision_history, (decision_entry,), limit=40),
            wake_record_links=tuple(await self._backend.list_refs("resident/wakeful/cycles")),
            workstream_links=tuple(
                await self._backend.list_refs("resident/domain-expert/workstreams")
            ),
            artifact_links=tuple(await self._backend.list_refs("resident/domain-expert/artifacts")),
            consolidation_links=tuple(
                await self._backend.list_refs("resident/domain-expert/consolidations")
            ),
        )
        return await self._backend.write_portfolio(portfolio)


class LocalCapabilityDiscoveryBackend(CapabilityDiscoveryPort):
    """Deterministic bounded capability discovery backend for local proof/tests."""

    async def discover(
        self,
        mandate: str,
        gap: ResidentCapabilityGap,
    ) -> ResidentCapabilityDiscoveryResult:
        capability = _compact_line(gap.capability or gap.summary, limit=120)
        safe_option = ResidentCapabilityOption(
            id=f"evaluate-{_slug(capability)}",
            title=f"Evaluate available capability path for {capability}",
            summary=(
                "Inspect existing tools, workflows, and documentation before building anything new."
            ),
            required_tools=("read_only_catalog_inspection",),
            required_workflows=("local_dry_run",),
            safe_next_experiment=(
                "Run a read-only catalog/workspace inspection and record whether a path exists."
            ),
            evidence=gap.source_evidence,
        )
        adapter_option = ResidentCapabilityOption(
            id=f"build-adapter-{_slug(capability)}",
            title=f"Build or wrap a capability adapter for {capability}",
            summary="Create the smallest adapter/workflow after existing paths are ruled out.",
            required_tools=("code", "tests"),
            required_workflows=("adapter_prototype",),
            required_adapters=("resident_capability_adapter",),
            risks=gap.risk_boundaries,
            approval_required=bool(gap.risk_boundaries),
            safe_next_experiment="Draft an adapter contract and dry-run it without side effects.",
            evidence=gap.source_evidence,
        )
        options = [safe_option, adapter_option]
        if gap.risk_boundaries:
            options.append(
                ResidentCapabilityOption(
                    id=f"approve-{_slug(capability)}",
                    title=f"Ask operator about risk boundaries for {capability}",
                    summary="Clarify human approval before touching bounded or external effects.",
                    risks=gap.risk_boundaries,
                    approval_required=True,
                    safe_next_experiment="Ask the operator for explicit approval boundaries.",
                    evidence=gap.source_evidence,
                )
            )
        return ResidentCapabilityDiscoveryResult(
            gap=gap,
            capability_summary=f"Capability gap: {capability}",
            why_it_matters=(
                "The resident cannot safely advance related objectives until this capability "
                "has a known path, adapter, or approval boundary."
            ),
            known_constraints=tuple(
                item
                for item in (
                    *gap.risk_boundaries,
                    *gap.blocked_dependencies,
                    *gap.required_capabilities,
                )
                if item
            ),
            candidate_options=tuple(options),
            recommended_option_id=safe_option.id,
            recommended_safe_next_experiment=safe_option.safe_next_experiment,
            unresolved_questions=(
                ("What approval boundary applies?",) if gap.risk_boundaries else ()
            ),
        )


class ResidentCapabilityDiscoveryRuntime:
    """Discovers ways to close resident capability gaps from portfolio state."""

    def __init__(
        self,
        *,
        backend: ResidentWorkItemBackend,
        discovery: CapabilityDiscoveryPort,
        config: ResidentCapabilityDiscoveryConfig | None = None,
    ) -> None:
        self._backend = backend
        self._discovery = discovery
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

    async def _persist_portfolio(
        self,
        mandate: str,
        objectives: tuple[ResidentObjective, ...],
        *,
        decision_entry: str,
    ) -> str:
        stored = await self._backend.read_portfolio(mandate)
        portfolio = stored or ResidentPortfolio(mandate=mandate)
        merged = merge_objectives(objectives)
        for objective in merged:
            await self._backend.write_objective(objective)
        portfolio = portfolio.with_objectives(
            merged,
            decision_history=_merge_text(portfolio.decision_history, (decision_entry,), limit=40),
            artifact_links=_merge_text(
                portfolio.artifact_links,
                tuple(await self._backend.list_refs(_CAPABILITY_DISCOVERY_PREFIX)),
            ),
        )
        return await self._backend.write_portfolio(portfolio)


class LocalSimulatedResidentExecutor(ResidentExecutionPort):
    """Deterministic resident execution backend for local proofs and tests."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[ResidentWorkerBrief, ResidentExecutionSession]] = {}
        self.launched: list[ResidentWorkerBrief] = []

    async def launch(self, brief: ResidentWorkerBrief) -> ResidentExecutionSession:
        session_id = f"local-{brief.id}"
        session = ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.COMPLETED.value,
            backend_name="local-simulated",
            summary=f"Simulated bounded execution for {brief.objective_title}.",
        )
        self._sessions[session_id] = (brief, session)
        self.launched.append(brief)
        return session

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        stored = self._sessions.get(session_id)
        if stored is None:
            return ResidentExecutionSession(
                session_id=session_id,
                status=ResidentDelegationStatus.UNAVAILABLE.value,
                backend_name="local-simulated",
                summary="session is not available in local simulator",
            )
        return stored[1]

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        stored = self._sessions.get(session_id)
        if stored is None:
            return None
        brief, session = stored
        if session.status != ResidentDelegationStatus.COMPLETED.value:
            return None
        return ResidentExecutionResult(
            session_id=session_id,
            status=ResidentDelegationStatus.COMPLETED.value,
            summary=f"Completed delegated work for {brief.objective_title}.",
            output_refs=(f"local-simulated/{brief.id}.md",),
            findings=(f"Delegated worker produced bounded evidence for {brief.objective_id}.",),
            follow_up_suggestions=(f"Review delegated output for {brief.objective_title}",),
            usage=ResidentBudgetSnapshot(turns_used=1),
        )

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        stored = self._sessions.get(session_id)
        brief = stored[0] if stored is not None else None
        session = ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.CANCELLED.value,
            backend_name="local-simulated",
            summary=reason,
        )
        if brief is not None:
            self._sessions[session_id] = (brief, session)
        return session


class WorkflowResidentExecutionAdapter(ResidentExecutionPort):
    """Resident execution adapter backed by an existing workflow capability port."""

    def __init__(
        self,
        *,
        workflows: WorkflowCapabilityPort,
        workflow_id: str = "",
        connection_id: str = "",
        repo: str = "",
        branch: str = "",
        model: str = "",
        definition: str = "",
    ) -> None:
        self._workflows = workflows
        self._workflow_id = workflow_id
        self._connection_id = connection_id
        self._repo = repo
        self._branch = branch
        self._model = model
        self._definition = definition
        self._references: dict[str, WorkflowRunReference] = {}

    async def launch(self, brief: ResidentWorkerBrief) -> ResidentExecutionSession:
        workflow_id = self._workflow_id or await self._select_workflow_id(brief)
        launch = await self._workflows.launch_workflow(
            WorkflowLaunchRequest(
                workflow_id=workflow_id,
                prompt=render_worker_brief(brief),
                session_name=_compact_line(f"resident-{brief.objective_id}", limit=63),
                repo=self._repo,
                branch=self._branch,
                connection_id=self._connection_id,
                model=self._model,
                definition=self._definition,
                provenance={
                    "resident_objective_id": brief.objective_id,
                    "resident_brief_id": brief.id,
                },
            )
        )
        reference = WorkflowRunReference(
            session_id=launch.session_id,
            slug=launch.slug,
            workflow_id=launch.workflow_id,
        )
        session_id = _workflow_reference_key(reference)
        self._references[session_id] = reference
        return ResidentExecutionSession(
            session_id=session_id,
            status=_delegation_status_from_external(launch.status),
            backend_name="workflow",
            summary=launch.workflow_name or launch.session_name or "workflow launched",
        )

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        reference = self._reference(session_id)
        status = await self._workflows.get_workflow_status(reference)
        return ResidentExecutionSession(
            session_id=status.session_id or session_id,
            status=_delegation_status_from_external(status.state),
            backend_name="workflow",
            summary=_workflow_status_summary(status),
        )

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        reference = self._reference(session_id)
        status = await self._workflows.get_workflow_status(reference)
        mapped = _delegation_status_from_external(status.state)
        events = await self._workflows.list_workflow_events(reference, limit=20)
        artifacts = await self._workflows.list_workflow_artifacts(reference)
        if mapped not in {
            ResidentDelegationStatus.COMPLETED.value,
            ResidentDelegationStatus.BLOCKED.value,
            ResidentDelegationStatus.FAILED.value,
        } and not artifacts:
            return None
        output_refs = tuple(item.path for item in artifacts if item.path)
        findings = tuple(
            _compact_line(
                f"{event.event_type}: {json.dumps(event.data, sort_keys=True)}",
                limit=240,
            )
            for event in events[:5]
        )
        result_status = _workflow_result_status(mapped, output_refs, events)
        if result_status == ResidentDelegationStatus.RUNNING.value and artifacts:
            findings = _merge_text(
                findings,
                (
                    "workflow artifact snapshot observed while session remained running; "
                    "terminal worker result still pending",
                ),
            )
        summary_parts = [
            _compact_line(item.summary or item.title or item.path, limit=160)
            for item in artifacts[:3]
            if item.summary or item.title or item.path
        ]
        summary = "; ".join(summary_parts) or _workflow_status_summary(status)
        return ResidentExecutionResult(
            session_id=status.session_id or session_id,
            status=result_status,
            summary=summary,
            output_refs=output_refs,
            findings=findings,
            follow_up_suggestions=(
                (f"Review workflow output for session {status.session_id or session_id}",)
                if output_refs or findings
                else ()
            ),
            blocked_reason=(
                summary
                if result_status
                in {
                    ResidentDelegationStatus.BLOCKED.value,
                    ResidentDelegationStatus.FAILED.value,
                }
                else ""
            ),
        )

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        reference = self._reference(session_id)
        status = await self._workflows.cancel_workflow(reference, reason=reason)
        return ResidentExecutionSession(
            session_id=status.session_id or session_id,
            status=_delegation_status_from_cancel(status.state),
            backend_name="workflow",
            summary=_workflow_status_summary(status),
        )

    async def _select_workflow_id(self, brief: ResidentWorkerBrief) -> str:
        workflows = await self._workflows.list_workflows()
        if not workflows:
            raise RuntimeError("No workflow capability is available for resident delegation")
        capability_words = {
            word
            for item in brief.constraints + brief.proof_criteria + (brief.objective_title,)
            for word in _slug(item).split("-")
            if len(word) > 3
        }
        scored = sorted(
            workflows,
            key=lambda item: len(
                capability_words
                & set(
                    _slug(
                        " ".join(
                            (
                                item.workflow_id,
                                item.name,
                                item.description,
                                " ".join(item.tags),
                            )
                        )
                    ).split("-")
                )
            ),
            reverse=True,
        )
        return scored[0].workflow_id

    def _reference(self, session_id: str) -> WorkflowRunReference:
        return (
            self._references.get(session_id)
            or _workflow_reference_from_key(session_id)
            or WorkflowRunReference(session_id=session_id)
        )


_WORKFLOW_REFERENCE_PREFIX = "workflow-ref:"


def _workflow_reference_key(reference: WorkflowRunReference) -> str:
    if reference.session_id:
        return reference.session_id
    payload = {
        key: value
        for key, value in {
            "session_id": reference.session_id,
            "slug": reference.slug,
            "workflow_id": reference.workflow_id,
        }.items()
        if value
    }
    if not payload:
        return reference.workflow_id or "workflow"
    return _WORKFLOW_REFERENCE_PREFIX + json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _workflow_reference_from_key(key: str) -> WorkflowRunReference | None:
    if not key.startswith(_WORKFLOW_REFERENCE_PREFIX):
        return None
    try:
        payload = json.loads(key.removeprefix(_WORKFLOW_REFERENCE_PREFIX))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return WorkflowRunReference(
        session_id=str(payload.get("session_id") or ""),
        slug=str(payload.get("slug") or ""),
        workflow_id=str(payload.get("workflow_id") or ""),
    )


_TERMINAL_DELEGATION_RESULT_STATUSES = frozenset(
    {
        ResidentDelegationStatus.COMPLETED.value,
        ResidentDelegationStatus.BLOCKED.value,
        ResidentDelegationStatus.FAILED.value,
        ResidentDelegationStatus.CANCELLED.value,
        ResidentDelegationStatus.NEEDS_OPERATOR.value,
        ResidentDelegationStatus.UNAVAILABLE.value,
    }
)


class LocalSubprocessResidentExecutor(ResidentExecutionPort):
    """Resident execution adapter that runs a local worker process."""

    def __init__(self, command: tuple[str, ...] | None = None) -> None:
        self._command = command or (sys.executable, "-c", _LOCAL_WORKER_SCRIPT)
        self._sessions: dict[str, ResidentExecutionSession] = {}
        self._results: dict[str, ResidentExecutionResult] = {}

    async def launch(self, brief: ResidentWorkerBrief) -> ResidentExecutionSession:
        session_id = f"subprocess-{brief.id}"
        proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(render_worker_brief(brief).encode("utf-8"))
        status = (
            ResidentDelegationStatus.COMPLETED.value
            if proc.returncode == 0
            else ResidentDelegationStatus.FAILED.value
        )
        summary, findings, follow_ups = _subprocess_payload(stdout, stderr)
        output_refs = (f"local-worker/{brief.id}.json",) if proc.returncode == 0 else ()
        result = ResidentExecutionResult(
            session_id=session_id,
            status=status,
            summary=summary,
            output_refs=output_refs,
            findings=findings,
            follow_up_suggestions=follow_ups,
            blocked_reason="" if proc.returncode == 0 else summary,
        )
        if output_refs:
            artifact_path = Path.cwd() / output_refs[0]
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(
                    {
                        "session_id": session_id,
                        "status": status,
                        "backend_name": "local-subprocess",
                        "summary": summary,
                        "findings": list(findings),
                        "follow_up_suggestions": list(follow_ups),
                        "brief": {
                            "id": brief.id,
                            "objective_id": brief.objective_id,
                            "objective_title": brief.objective_title,
                            "desired_outcome": brief.desired_outcome,
                            "proof_criteria": list(brief.proof_criteria),
                            "evidence": list(brief.evidence),
                            "artifact_links": list(brief.artifact_links),
                            "constraints": list(brief.constraints),
                            "risk_boundaries": list(brief.risk_boundaries),
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        session = ResidentExecutionSession(
            session_id=session_id,
            status=status,
            backend_name="local-subprocess",
            summary=summary,
        )
        self._sessions[session_id] = session
        self._results[session_id] = result
        return session

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        return self._sessions.get(
            session_id,
            ResidentExecutionSession(
                session_id=session_id,
                status=ResidentDelegationStatus.UNAVAILABLE.value,
                backend_name="local-subprocess",
                summary="local worker session is not available",
            ),
        )

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        return self._results.get(session_id)

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        session = ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.CANCELLED.value,
            backend_name="local-subprocess",
            summary=reason,
        )
        self._sessions[session_id] = session
        return session


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
        if self._expert_memory is None:
            return ()
        existing = await self._expert_memory.read_domain_model(mandate)
        model = existing or ResidentDomainModel(mandate=mandate)
        updated_model, consolidation_result = _domain_model_with_delegation_learning(
            model,
            record=record,
            result=result,
            review=review,
            result_ref=result_ref,
            review_ref=review_ref,
            follow_ups=follow_ups,
        )
        model_ref = await self._expert_memory.write_domain_model(updated_model)
        consolidation_ref = await self._expert_memory.write_consolidation(
            updated_model,
            consolidation_result,
        )
        return (model_ref, consolidation_ref)

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
        stored = await self._backend.read_portfolio(mandate)
        portfolio = stored or ResidentPortfolio(mandate=mandate)
        merged = merge_objectives(objectives)
        for objective in merged:
            await self._backend.write_objective(objective)
        portfolio = portfolio.with_objectives(
            merged,
            decision_history=_merge_text(portfolio.decision_history, (decision_entry,), limit=40),
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
            objective = _gated_objective_for_question(delegation, question)
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


def discover_objectives(
    mandate: str,
    *,
    portfolio: ResidentPortfolio,
    evidence: ResidentPortfolioEvidence,
) -> tuple[ResidentObjective, ...]:
    candidates: list[ResidentObjective] = []
    completed = tuple(
        objective for objective in portfolio.objectives if objective.status == "completed"
    )
    model = evidence.domain_model
    if model is not None:
        for gap in model.capability_gaps[:6]:
            candidates.append(
                _objective_from_text(
                    mandate,
                    title=f"Close capability gap: {gap}",
                    source=gap,
                    kind=_kind_for_text(gap),
                    reasoning="Capability gaps are remembered resident evidence.",
                    proof="Capability gap has an artifact, decision, or working path.",
                )
            )
        for question in model.open_questions[:6]:
            candidates.append(
                _objective_from_text(
                    mandate,
                    title=f"Resolve open question: {question}",
                    source=question,
                    kind=ResidentObjectiveKind.OPERATOR_QUESTION
                    if _needs_human_answer(question)
                    else ResidentObjectiveKind.RESEARCH,
                    status=ResidentObjectiveStatus.NEEDS_OPERATOR
                    if _needs_human_answer(question)
                    else ResidentObjectiveStatus.CANDIDATE,
                    pending_question=question if _needs_human_answer(question) else "",
                    reasoning="Open questions represent unresolved resident uncertainty.",
                    proof="Question is answered, retired, or converted into a next objective.",
                )
            )
        for opportunity in model.opportunities[:6]:
            candidates.append(
                _objective_from_text(
                    mandate,
                    title=f"Advance opportunity: {opportunity}",
                    source=opportunity,
                    kind=_kind_for_text(opportunity),
                    reasoning="Domain opportunities are remembered possible work.",
                    proof="Opportunity has been advanced into an artifact or workstream.",
                )
            )
    for workstream in evidence.workstreams:
        if workstream.status in {
            ResidentWorkstreamStatus.PROPOSED.value,
            ResidentWorkstreamStatus.ACTIVE.value,
            ResidentWorkstreamStatus.PAUSED.value,
        }:
            candidates.append(
                _objective_from_text(
                    mandate,
                    title=f"Resume workstream: {workstream.title}",
                    source=f"{workstream.id}: {workstream.status}",
                    kind=_kind_for_text(workstream.kind),
                    reasoning="Actionable workstreams should remain visible in long-horizon work.",
                    proof="Workstream advances, completes, or produces a clear blocked reason.",
                    dependencies=(),
                )
            )
    for record in evidence.wake_records[:5]:
        reason = str(getattr(record, "attention_reason", "")).strip()
        if reason:
            candidates.append(
                _objective_from_text(
                    mandate,
                    title=f"Review wake outcome: {reason}",
                    source=reason,
                    kind=ResidentObjectiveKind.REVIEW,
                    reasoning="Wake records expose recurring attention and review needs.",
                    proof="Wake outcome is reviewed and either retired or converted into work.",
                )
            )
    for ref in evidence.artifact_refs[:5]:
        candidates.append(
            _objective_from_text(
                mandate,
                title=f"Review artifact: {_basename(ref)}",
                source=ref,
                kind=ResidentObjectiveKind.REVIEW,
                reasoning="Durable artifacts should feed future prioritization.",
                proof="Artifact is reviewed and linked to an objective decision.",
            )
        )
    if completed:
        latest = completed[-1]
        candidates.append(
            _objective_from_text(
                mandate,
                title=f"Build on completed milestone: {latest.title}",
                source=latest.id,
                kind=ResidentObjectiveKind.CONSOLIDATION,
                reasoning="Completed milestones should influence the next objective.",
                proof="A follow-up objective is selected using completed milestone evidence.",
                dependencies=(latest.id,),
            )
        )
    return tuple(merge_objectives(tuple(candidates)))


def prioritize_objectives(
    objectives: tuple[ResidentObjective, ...],
    *,
    mandate: str,
) -> tuple[ResidentObjective, ...]:
    completed_ids = {
        objective.id
        for objective in objectives
        if objective.status == ResidentObjectiveStatus.COMPLETED.value
    }
    scored = tuple(
        _score_objective(objective, completed_ids=completed_ids, mandate=mandate)
        for objective in objectives
    )
    return tuple(sorted(scored, key=lambda item: item.priority_score, reverse=True))


def select_objectives(
    objectives: tuple[ResidentObjective, ...],
    *,
    max_selected: int,
    max_active: int,
) -> tuple[ResidentObjective, ...]:
    if max_selected <= 0 or max_active <= 0:
        return ()
    active_count = sum(
        1 for objective in objectives if objective.status == ResidentObjectiveStatus.ACTIVE.value
    )
    remaining_active = max(0, max_active - active_count)
    limit = min(max_selected, remaining_active or max_selected)
    selected: list[ResidentObjective] = []
    completed_ids = {
        objective.id
        for objective in objectives
        if objective.status == ResidentObjectiveStatus.COMPLETED.value
    }
    for objective in objectives:
        if objective.status in {
            ResidentObjectiveStatus.CANCELLED.value,
            ResidentObjectiveStatus.SUPERSEDED.value,
            ResidentObjectiveStatus.COMPLETED.value,
            ResidentObjectiveStatus.BLOCKED.value,
        }:
            continue
        if objective.dependencies and not set(objective.dependencies).issubset(completed_ids):
            continue
        selected.append(objective)
        if len(selected) >= limit:
            break
    return tuple(selected)


def _should_bootstrap_portfolio(
    *,
    portfolio: ResidentPortfolio,
    evidence: ResidentPortfolioEvidence,
    discovered: tuple[ResidentObjective, ...],
    enabled: bool,
) -> bool:
    if not enabled:
        return False
    if portfolio.objectives or discovered:
        return False
    if evidence.domain_model is not None:
        return False
    if evidence.workstreams or evidence.wake_records:
        return False
    if evidence.artifact_refs or evidence.consolidation_refs:
        return False
    return True


def _bootstrap_decision_entries(wake_run: WakefulResidentRun) -> tuple[str, ...]:
    if not wake_run.cycles:
        return ("bootstrap wakeful orientation produced no wake cycles",)
    entries: list[str] = []
    for cycle in wake_run.cycles:
        entries.append(
            "bootstrap wakeful orientation "
            f"cycle {cycle.cycle_number}: {cycle.decision.value}; {cycle.attention_reason}"
        )
    return tuple(_compact_line(item, limit=220) for item in entries)


def select_delegation_candidates(
    objectives: tuple[ResidentObjective, ...],
    *,
    delegations: tuple[ResidentDelegationRecord, ...],
    mandate: str,
    max_selected: int,
    approved_risk_objective_ids: tuple[str, ...] = (),
) -> tuple[tuple[ResidentObjective, ...], tuple[ResidentObjective, ...]]:
    """Select objectives ready for delegated execution and separate gated work."""

    if max_selected <= 0:
        return (), ()
    active_sources = {
        item.source_objective_id
        for item in delegations
        if item.status
        in {
            ResidentDelegationStatus.LAUNCHED.value,
            ResidentDelegationStatus.RUNNING.value,
        }
    }
    completed_ids = {
        objective.id
        for objective in objectives
        if objective.status == ResidentObjectiveStatus.COMPLETED.value
    }
    selected: list[ResidentObjective] = []
    gated: list[ResidentObjective] = []
    approved_risk_ids = set(approved_risk_objective_ids)
    prioritized = prioritize_objectives(objectives, mandate=mandate)
    for objective in prioritized:
        if objective.id in active_sources:
            continue
        if not _is_delegation_ready(objective, completed_ids):
            continue
        if objective.risk_boundaries and objective.id not in approved_risk_ids:
            gated.append(objective)
            continue
        if len(selected) < max_selected:
            selected.append(objective)
    return tuple(selected), tuple(gated)


def _active_unresolved_delegation_count(
    delegations: tuple[ResidentDelegationRecord, ...],
) -> int:
    return sum(1 for item in delegations if _active_unresolved_delegation(item))


def _active_unresolved_delegation(delegation: ResidentDelegationRecord) -> bool:
    return (
        not delegation.result_refs
        and delegation.status
        in {
            ResidentDelegationStatus.LAUNCHED.value,
            ResidentDelegationStatus.RUNNING.value,
        }
    )


def _delegation_keep_sort_key(delegation: ResidentDelegationRecord) -> tuple[int, datetime, str]:
    status_rank = 0 if delegation.status == ResidentDelegationStatus.RUNNING.value else 1
    return (status_rank, delegation.created_at, delegation.id)


def merge_objectives(objectives: tuple[ResidentObjective, ...]) -> tuple[ResidentObjective, ...]:
    by_id: dict[str, ResidentObjective] = {}
    for objective in objectives:
        existing = by_id.get(objective.id)
        if existing is None:
            by_id[objective.id] = objective
            continue
        kept, duplicate = (
            (objective, existing)
            if _status_rank(objective.status) > _status_rank(existing.status)
            else (existing, objective)
        )
        by_id[kept.id] = kept.with_updates(
            supersedes=_merge_text(kept.supersedes, (duplicate.id,)),
            source_evidence=_merge_text(kept.source_evidence, duplicate.source_evidence),
        )
    return tuple(by_id.values())


def detect_capability_gaps(
    objectives: tuple[ResidentObjective, ...],
) -> tuple[ResidentCapabilityGap, ...]:
    """Extract generic capability gaps from resident portfolio objectives."""

    known_ids = {objective.id for objective in objectives}
    gaps: list[ResidentCapabilityGap] = []
    for objective in objectives:
        gap_sources = _capability_gap_sources(objective, known_ids)
        for source, reason in gap_sources:
            capability = _capability_name_from_text(source) or _compact_line(source, limit=80)
            gap_id = _slug(f"{objective.id}-{capability}") or f"{objective.id}-capability-gap"
            gaps.append(
                ResidentCapabilityGap(
                    id=gap_id,
                    capability=capability,
                    summary=_compact_line(source, limit=220),
                    source_objective_id=objective.id,
                    source_evidence=_merge_text(objective.source_evidence, (source,)),
                    required_capabilities=objective.required_capabilities,
                    risk_boundaries=objective.risk_boundaries,
                    blocked_dependencies=tuple(
                        dep for dep in objective.dependencies if dep not in known_ids
                    ),
                    reason=reason,
                )
            )
    return tuple(_dedupe_gaps(gaps))


def render_capability_discovery_result(result: ResidentCapabilityDiscoveryResult) -> str:
    return (
        f"# Capability Discovery: {result.gap.capability}\n\n"
        f"- gap_id: {result.gap.id}\n"
        f"- source_objective_id: {result.gap.source_objective_id}\n"
        f"- recommended_option_id: {result.recommended_option_id}\n"
        f"- budget_notes: {result.budget_notes}\n\n"
        f"## Existing Capability Check\n\n{_render_list(result.existing_capabilities)}\n\n"
        f"## Duplicate Check Notes\n\n{_render_list(result.duplicate_check_notes)}\n\n"
        f"## Research Evidence\n\n{_render_list(result.research_evidence)}\n\n"
        f"## Configuration Evidence\n\n{_render_list(result.configuration_evidence)}\n\n"
        f"## Capability Summary\n\n{result.capability_summary}\n\n"
        f"## Why It Matters\n\n{result.why_it_matters}\n\n"
        f"## Known Constraints\n\n{_render_list(result.known_constraints)}\n\n"
        "## Candidate Options\n\n"
        f"{_render_list(_capability_option_line(item) for item in result.candidate_options)}\n\n"
        "## Recommended Safe Next Experiment\n\n"
        f"{result.recommended_safe_next_experiment}\n\n"
        f"## Unresolved Questions\n\n{_render_list(result.unresolved_questions)}\n\n"
        f"## Source Evidence\n\n{_render_list(result.gap.source_evidence)}\n"
    )


def render_worker_brief(brief: ResidentWorkerBrief) -> str:
    return (
        f"# Resident Worker Brief: {brief.objective_title}\n\n"
        f"- brief_id: {brief.id}\n"
        f"- objective_id: {brief.objective_id}\n\n"
        f"## Resident Mandate\n\n{brief.mandate}\n\n"
        f"## Source Objective\n\n{brief.objective_title}\n\n"
        f"## Desired Outcome\n\n{brief.desired_outcome}\n\n"
        f"## Proof Criteria\n\n{_render_list(brief.proof_criteria)}\n\n"
        f"## Evidence\n\n{_render_list(brief.evidence)}\n\n"
        f"## Artifact Links\n\n{_render_list(brief.artifact_links)}\n\n"
        f"## Constraints\n\n{_render_list(brief.constraints)}\n\n"
        f"## Risk Boundaries\n\n{_render_list(brief.risk_boundaries)}\n\n"
        f"## Expected Output Shape\n\n{brief.expected_output_shape}\n"
    )


def render_delegation_result(
    delegation: ResidentDelegationRecord,
    result: ResidentExecutionResult,
) -> str:
    return (
        f"# Delegated Result: {delegation.brief.objective_title}\n\n"
        f"- delegation_id: {delegation.id}\n"
        f"- source_objective_id: {delegation.source_objective_id}\n"
        f"- session_id: {result.session_id}\n"
        f"- status: {result.status}\n"
        f"- backend_name: {delegation.backend_name}\n\n"
        f"## Summary\n\n{result.summary}\n\n"
        f"## Output Refs\n\n{_render_list(result.output_refs)}\n\n"
        f"## Findings\n\n{_render_list(result.findings)}\n\n"
        f"## Follow-Up Suggestions\n\n{_render_list(result.follow_up_suggestions)}\n\n"
        f"## Blocked Reason\n\n{result.blocked_reason or 'none'}\n\n"
        f"## Worker Brief\n\n{render_worker_brief(delegation.brief)}"
    )


def render_delegation_review(review: ResidentDelegationReview) -> str:
    return (
        f"# Delegated Result Review: {review.delegation_id}\n\n"
        f"- id: {review.id}\n"
        f"- delegation_id: {review.delegation_id}\n"
        f"- source_objective_id: {review.source_objective_id}\n"
        f"- result_session_id: {review.result_session_id}\n"
        f"- decision: {review.decision}\n"
        f"- created_at: {review.created_at.isoformat()}\n\n"
        f"## Reason\n\n{review.reason}\n\n"
        f"## Proof Criteria Checked\n\n{_render_list(review.proof_criteria_checked)}\n\n"
        f"## Missing Evidence\n\n{_render_list(review.missing_evidence)}\n\n"
        f"## Follow-Up Suggestions\n\n{_render_list(review.follow_up_suggestions)}\n"
    )


def review_delegation_result(
    source: ResidentObjective | None,
    delegation: ResidentDelegationRecord,
    result: ResidentExecutionResult,
) -> ResidentDelegationReview:
    proof = source.proof_criteria if source is not None else delegation.brief.proof_criteria
    missing: list[str] = []
    if result.blocked_reason:
        decision = ResidentDelegationReviewDecision.BLOCKED.value
        reason = f"Delegated result reported a blocker: {result.blocked_reason}"
    elif result.status in {
        ResidentDelegationStatus.LAUNCHED.value,
        ResidentDelegationStatus.RUNNING.value,
    }:
        decision = ResidentDelegationReviewDecision.NEEDS_FOLLOW_UP.value
        reason = f"Delegated result is still {result.status}; terminal evidence is pending."
        missing.append("terminal worker status")
    elif result.status in {
        ResidentDelegationStatus.FAILED.value,
        ResidentDelegationStatus.UNAVAILABLE.value,
    }:
        decision = ResidentDelegationReviewDecision.RETRY.value
        reason = f"Delegated result ended with status {result.status}."
    else:
        if not result.summary.strip():
            missing.append("summary")
        if not result.output_refs and not result.findings:
            missing.append("output refs or findings")
        if missing:
            decision = ResidentDelegationReviewDecision.NEEDS_FOLLOW_UP.value
            reason = "Delegated result needs follow-up evidence before completion."
        else:
            decision = ResidentDelegationReviewDecision.COMPLETE.value
            reason = "Delegated result satisfies the bounded review checks" + (
                " and produced follow-up work." if result.follow_up_suggestions else "."
            )
    return ResidentDelegationReview(
        id=_slug(f"review-{delegation.id}-{result.session_id}") or f"review-{delegation.id}",
        delegation_id=delegation.id,
        source_objective_id=delegation.source_objective_id,
        result_session_id=result.session_id,
        decision=decision,
        reason=reason,
        proof_criteria_checked=proof,
        missing_evidence=tuple(missing),
        follow_up_suggestions=result.follow_up_suggestions,
    )


def build_worker_brief(mandate: str, objective: ResidentObjective) -> ResidentWorkerBrief:
    constraints = _merge_text(
        objective.required_capabilities,
        (f"budget estimate: {objective.budget_estimate}",),
    )
    return ResidentWorkerBrief(
        id=_slug(f"brief-{objective.id}") or f"brief-{objective.id}",
        mandate=mandate,
        objective_id=objective.id,
        objective_title=objective.title,
        desired_outcome=objective.expected_outcome,
        proof_criteria=objective.proof_criteria,
        evidence=objective.source_evidence,
        artifact_links=_merge_text(
            objective.artifact_links,
            objective.wake_links,
            objective.workstream_links,
            objective.consolidation_links,
        ),
        constraints=constraints,
        risk_boundaries=objective.risk_boundaries,
        expected_output_shape=(
            "Return a concise summary, output references, findings, blocked reason "
            "if any, and suggested follow-up work."
        ),
    )


def _capability_gap_sources(
    objective: ResidentObjective,
    known_ids: set[str],
) -> tuple[tuple[str, str], ...]:
    sources: list[tuple[str, str]] = []
    for capability in objective.required_capabilities:
        sources.append((capability, "objective declares a required capability"))
    for dependency in objective.dependencies:
        if dependency not in known_ids:
            sources.append((dependency, "objective depends on a missing objective/capability"))
    for text in (
        objective.title,
        objective.purpose,
        objective.expected_outcome,
        objective.reasoning,
        *objective.source_evidence,
        *objective.proof_progress,
    ):
        lowered = str(text).casefold()
        if _has_any(lowered, ("missing capability", "capability gap", "missing tool")):
            sources.append((str(text), "objective evidence names a capability gap"))
        elif _has_any(lowered, ("missing workflow", "missing adapter", "no safe execution path")):
            sources.append((str(text), "objective evidence names a missing execution path"))
    if objective.status == ResidentObjectiveStatus.BLOCKED.value and _has_any(
        _objective_text(objective),
        ("capability", "tool", "workflow", "adapter", "execution path"),
    ):
        sources.append(
            (objective.reasoning or objective.title, "blocked objective lacks capability path")
        )
    return tuple(sources)


def _dedupe_gaps(gaps: list[ResidentCapabilityGap]) -> list[ResidentCapabilityGap]:
    seen: set[str] = set()
    unique: list[ResidentCapabilityGap] = []
    for gap in gaps:
        key = gap.id
        if key in seen:
            continue
        seen.add(key)
        unique.append(gap)
    return unique


def _capability_name_from_text(text: str) -> str:
    value = _compact_line(text, limit=120)
    lowered = value.casefold()
    for marker in (
        "missing capability:",
        "capability gap:",
        "missing tool:",
        "missing workflow:",
        "missing adapter:",
    ):
        if marker in lowered:
            idx = lowered.index(marker) + len(marker)
            return _compact_line(value[idx:].strip(" :-"), limit=100)
    return value


def _limit_discovery_options(
    result: ResidentCapabilityDiscoveryResult,
    limit: int,
) -> ResidentCapabilityDiscoveryResult:
    if limit <= 0 or len(result.candidate_options) <= limit:
        return result
    return ResidentCapabilityDiscoveryResult(
        gap=result.gap,
        capability_summary=result.capability_summary,
        why_it_matters=result.why_it_matters,
        known_constraints=result.known_constraints,
        candidate_options=result.candidate_options[:limit],
        recommended_option_id=result.recommended_option_id,
        recommended_safe_next_experiment=result.recommended_safe_next_experiment,
        unresolved_questions=result.unresolved_questions,
        budget_notes=result.budget_notes,
        existing_capabilities=result.existing_capabilities,
        duplicate_check_notes=result.duplicate_check_notes,
        research_evidence=result.research_evidence,
        configuration_evidence=result.configuration_evidence,
    )


def _objectives_from_capability_discovery(
    result: ResidentCapabilityDiscoveryResult,
    *,
    limit: int,
) -> tuple[ResidentObjective, ...]:
    objectives: list[ResidentObjective] = []
    for option in result.candidate_options:
        status = (
            ResidentObjectiveStatus.NEEDS_OPERATOR.value
            if option.approval_required
            else ResidentObjectiveStatus.CANDIDATE.value
        )
        kind = (
            ResidentObjectiveKind.OPERATOR_QUESTION.value
            if option.approval_required
            else ResidentObjectiveKind.TOOL_BUILDING.value
            if option.required_adapters
            else ResidentObjectiveKind.VERIFICATION.value
        )
        pending_question = (
            f"What approval boundary applies before using option '{option.title}'?"
            if option.approval_required
            else ""
        )
        objective_id = _slug(f"capability-{option.id}") or "capability-discovery-follow-up"
        objectives.append(
            ResidentObjective(
                id=objective_id,
                title=option.title,
                purpose=option.summary,
                serves_mandate_because=result.why_it_matters,
                expected_outcome=option.safe_next_experiment
                or "A safe capability path is evaluated.",
                proof_criteria=(
                    option.safe_next_experiment
                    or "The option is evaluated and linked to a decision.",
                ),
                kind=kind,
                dependencies=(result.gap.source_objective_id,)
                if result.gap.source_objective_id
                else (),
                required_capabilities=option.required_tools
                + option.required_workflows
                + option.required_adapters,
                risk_boundaries=option.risks,
                status=status,
                source_evidence=option.evidence or result.gap.source_evidence,
                reasoning=(
                    f"Capability discovery option for {result.gap.capability}: {option.summary}"
                ),
                pending_question=pending_question,
            )
        )
        if len(objectives) >= limit:
            break
    return tuple(objectives)


def _capability_option_line(option: ResidentCapabilityOption) -> str:
    approval = "approval required" if option.approval_required else "safe/read-only first"
    tools = ", ".join(option.required_tools + option.required_workflows + option.required_adapters)
    risks = ", ".join(option.risks) or "none"
    config = ""
    if option.configuration:
        config = f"; config={json.dumps(option.configuration, sort_keys=True)}"
    return (
        f"{option.id}: {option.title}; {approval}; "
        f"tools={tools or 'none'}; risks={risks}{config}"
    )


_LOCAL_WORKER_SCRIPT = r"""
import json
import sys

brief = sys.stdin.read()
lines = [line for line in brief.splitlines() if line.strip()]
payload = {
    "summary": f"Processed resident worker brief with {len(lines)} non-empty lines.",
    "findings": [
        "worker brief was executed by a local subprocess",
        f"brief characters: {len(brief)}",
    ],
    "follow_up_suggestions": [
        "Review local worker output and decide the next bounded resident objective"
    ],
}
print(json.dumps(payload, sort_keys=True))
"""


def _delegation_status_from_external(value: str) -> str:
    lowered = str(value or "").casefold()
    if lowered in {"completed", "complete", "succeeded", "success", "stopped", "archived"}:
        return ResidentDelegationStatus.COMPLETED.value
    if lowered in {"failed", "error"}:
        return ResidentDelegationStatus.FAILED.value
    if lowered in {"blocked", "needs_operator"}:
        return ResidentDelegationStatus.BLOCKED.value
    if lowered in {"cancelled", "canceled"}:
        return ResidentDelegationStatus.CANCELLED.value
    if lowered in {"running", "active", "in_progress", "started"}:
        return ResidentDelegationStatus.RUNNING.value
    return ResidentDelegationStatus.LAUNCHED.value


def _workflow_result_status(
    mapped_status: str,
    output_refs: tuple[str, ...],
    events: list[WorkflowRunEvent],
) -> str:
    if mapped_status == ResidentDelegationStatus.COMPLETED.value:
        return mapped_status
    if any(_workflow_completion_event(event) for event in events):
        return ResidentDelegationStatus.COMPLETED.value
    if any(_workflow_completion_artifact(path) for path in output_refs):
        return ResidentDelegationStatus.COMPLETED.value
    return mapped_status


def _workflow_completion_event(event: WorkflowRunEvent) -> bool:
    event_type = str(event.event_type or "").casefold()
    if not event_type:
        return False
    return event_type.endswith(".publish.completed") or event_type in {
        "workflow.completed",
        "research.completed",
    }


def _workflow_completion_artifact(path: str) -> bool:
    name = Path(path).name.casefold()
    return name in {"manifest.md", "summary.md"}


def _delegation_status_from_cancel(value: str) -> str:
    lowered = str(value or "").casefold()
    if lowered in {"stopped", "cancelled", "canceled"}:
        return ResidentDelegationStatus.CANCELLED.value
    return _delegation_status_from_external(value)


def _workflow_status_summary(status: WorkflowRunStatus) -> str:
    error = str(status.raw.get("error") or status.raw.get("detail") or "").strip()
    if error:
        return error
    return status.workflow_name or status.session_name or status.state or "workflow result observed"


def _subprocess_payload(
    stdout: bytes,
    stderr: bytes,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    raw = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"summary": raw}
    else:
        data = {"summary": err or "local worker produced no output"}
    summary = _compact_line(str(data.get("summary") or err or raw), limit=240)
    findings = tuple(str(item) for item in data.get("findings", ()) if str(item).strip())
    follow_ups = tuple(
        str(item) for item in data.get("follow_up_suggestions", ()) if str(item).strip()
    )
    if err:
        findings = _merge_text(findings, (f"stderr: {_compact_line(err, limit=180)}",))
    return summary, findings, follow_ups


def _is_delegation_ready(
    objective: ResidentObjective,
    completed_ids: set[str],
) -> bool:
    if objective.superseded_by:
        return False
    if objective.status not in {
        ResidentObjectiveStatus.CANDIDATE.value,
        ResidentObjectiveStatus.ACTIVE.value,
        ResidentObjectiveStatus.PAUSED.value,
    }:
        return False
    if objective.dependencies and not set(objective.dependencies).issubset(completed_ids):
        return False
    return bool(objective.proof_criteria)


def _delegation_id(objective: ResidentObjective) -> str:
    return _slug(f"delegation-{objective.id}") or f"delegation-{objective.id}"


def _delegation_reason(objective: ResidentObjective) -> str:
    return (
        objective.priority_rationale
        or objective.reasoning
        or "Objective is ready for bounded delegated execution."
    )


def _delegation_operator_question(objective: ResidentObjective) -> str:
    risks = ", ".join(objective.risk_boundaries) or "bounded risk"
    return f"May I delegate '{objective.title}' despite these risk boundaries: {risks}?"


def _delegation_operator_objective(
    mandate: str,
    objective: ResidentObjective,
    *,
    question: str,
) -> ResidentObjective:
    return _objective_from_text(
        mandate,
        title=f"Ask operator before delegation: {objective.title}",
        source=question,
        kind=ResidentObjectiveKind.OPERATOR_QUESTION,
        reasoning="Delegated execution would cross a configured risk boundary.",
        proof="Operator decision is recorded before delegated execution proceeds.",
        status=ResidentObjectiveStatus.NEEDS_OPERATOR,
        dependencies=(objective.id,),
        pending_question=question,
    )


def _gated_objective_for_question(
    report: ResidentDelegationReport,
    question: str,
) -> ResidentObjective | None:
    for objective in report.skipped_or_gated_objectives:
        if _delegation_operator_question(objective) == question:
            return objective
    return None


def _operator_contact_request(
    mandate: str,
    question: str,
    objective: ResidentObjective | None,
) -> OperatorContactRequest:
    objective_id = objective.id if objective is not None else ""
    contact_id = _slug(f"operator-contact-{objective_id or question}") or "operator-contact"
    risks = objective.risk_boundaries if objective is not None else ()
    return OperatorContactRequest(
        id=contact_id,
        question=question,
        reason=(
            "Resident delegated execution would cross a risk boundary and needs "
            "operator judgment before continuing."
        ),
        impact=(
            "A positive answer allows this specific objective to be delegated; "
            "no answer keeps the resident waiting."
        ),
        source_objective_id=objective_id,
        risk_boundaries=risks,
        kind=OperatorContactKind.HELP_NEEDED,
        purpose=OperatorContactPurpose.APPROVAL,
    )


def _autonomy_final_suggestion(
    cycles: list[ResidentAutonomyCycleReport],
    contacts: list[OperatorContactResult],
) -> str:
    pending = next(
        (
            item
            for item in reversed(contacts)
            if item.status == OperatorContactStatus.PENDING.value
        ),
        None,
    )
    if pending is not None:
        return f"Waiting for operator answer: {pending.request.question}"
    if cycles:
        return cycles[-1].final_suggested_next_action
    return "No autonomy cycles were allowed by config."


def _render_operator_contact(result: OperatorContactResult) -> str:
    approved = "unknown" if result.approved is None else "true" if result.approved else "false"
    responded_at = result.responded_at.isoformat() if result.responded_at else ""
    return (
        f"# Resident Operator Contact: {result.request.question}\n\n"
        f"- id: {result.request.id}\n"
        f"- status: {result.status}\n"
        f"- contact_kind: {result.request.kind.value}\n"
        f"- contact_purpose: {result.request.purpose.value}\n"
        f"- source_objective_id: {result.request.source_objective_id}\n"
        f"- approved: {approved}\n"
        f"- emitted_ref: {result.emitted_ref}\n"
        f"- created_at: {result.request.created_at.isoformat()}\n"
        f"- responded_at: {responded_at}\n\n"
        f"## Question\n\n{result.request.question}\n\n"
        f"## Reason\n\n{result.request.reason}\n\n"
        f"## Impact\n\n{result.request.impact}\n\n"
        f"## Risk Boundaries\n\n{_render_list(result.request.risk_boundaries)}\n\n"
        f"## Answer\n\n{result.answer or 'none'}\n"
    )


def _absorb_delegation_result(
    objective: ResidentObjective,
    result: ResidentExecutionResult,
    review: ResidentDelegationReview,
    result_ref: str,
    *,
    consolidation_refs: tuple[str, ...] = (),
) -> ResidentObjective:
    if review.decision == ResidentDelegationReviewDecision.COMPLETE.value:
        status = ResidentObjectiveStatus.COMPLETED.value
    elif review.decision == ResidentDelegationReviewDecision.BLOCKED.value:
        status = ResidentObjectiveStatus.BLOCKED.value
    elif review.decision == ResidentDelegationReviewDecision.ASK_OPERATOR.value:
        status = ResidentObjectiveStatus.NEEDS_OPERATOR.value
    else:
        status = ResidentObjectiveStatus.PAUSED.value
    progress = _merge_text(
        objective.proof_progress,
        (f"delegation result {result.session_id}: {result.status}: {result.summary}",),
        (f"delegation review {review.id}: {review.decision}: {review.reason}",),
        result.findings,
    )
    return objective.with_updates(
        status=status,
        proof_progress=progress,
        artifact_links=_merge_text(objective.artifact_links, (result_ref,), result.output_refs),
        consolidation_links=_merge_text(objective.consolidation_links, consolidation_refs),
        last_reviewed_at=datetime.now(UTC),
    )


def _domain_model_with_delegation_learning(
    model: ResidentDomainModel,
    *,
    record: ResidentDelegationRecord,
    result: ResidentExecutionResult,
    review: ResidentDelegationReview,
    result_ref: str,
    review_ref: str,
    follow_ups: tuple[ResidentObjective, ...],
) -> tuple[ResidentDomainModel, WorkstreamExecutionResult]:
    summary = _compact_line(
        f"delegation {record.id} reviewed as {review.decision}: {result.summary or review.reason}",
        limit=240,
    )
    facts = (
        result.findings
        if review.decision == ResidentDelegationReviewDecision.COMPLETE.value
        else ()
    )
    gaps = _delegation_capability_gaps(result, review)
    failure_notes = _delegation_failure_notes(record, result, review)
    open_threads = _merge_text(
        result.follow_up_suggestions,
        tuple(f"follow-up objective {item.id}: {item.title}" for item in follow_ups),
    )
    artifact_refs = _merge_text((result_ref, review_ref), result.output_refs)
    consolidation_result = WorkstreamExecutionResult(
        workstream_id=record.id,
        status=result.status,
        summary=summary,
        artifact_refs=artifact_refs,
        facts=facts,
        lessons=open_threads,
        capability_gaps=gaps,
        usage=result.usage,
    )
    artifact = ExpertArtifact(
        title=f"Delegation review: {record.brief.objective_title}",
        kind="delegation_review",
        path=review_ref,
        purpose="Compact resident memory for a reviewed delegated work result.",
        summary=review.reason,
    )
    updated = model.with_consolidation(
        recent_outcomes=_merge_text(model.recent_outcomes, (summary,), limit=12),
        known_facts=_merge_text(model.known_facts, facts),
        resident_decisions=_merge_text(
            model.resident_decisions,
            (f"reviewed delegation {record.id}: {review.decision}",),
        ),
        failure_notes=_merge_text(model.failure_notes, failure_notes),
        open_threads=_merge_text(model.open_threads, open_threads),
        capability_gaps=_merge_text(model.capability_gaps, gaps),
        artifacts=_merge_expert_artifacts(model.artifacts, (artifact,)),
    )
    return updated, consolidation_result


def _delegation_capability_gaps(
    result: ResidentExecutionResult,
    review: ResidentDelegationReview,
) -> tuple[str, ...]:
    if review.decision == ResidentDelegationReviewDecision.COMPLETE.value:
        return ()
    text = " ".join((result.summary, result.blocked_reason, review.reason)).casefold()
    if not _has_any(text, ("capability", "tool", "workflow", "adapter", "unavailable")):
        return ()
    return (
        _compact_line(
            result.blocked_reason or review.reason or result.summary,
            limit=180,
        ),
    )


def _delegation_failure_notes(
    record: ResidentDelegationRecord,
    result: ResidentExecutionResult,
    review: ResidentDelegationReview,
) -> tuple[str, ...]:
    if review.decision == ResidentDelegationReviewDecision.COMPLETE.value:
        return ()
    return (
        _compact_line(
            f"delegation {record.id} {result.status}: "
            f"{result.blocked_reason or review.reason or result.summary}",
            limit=220,
        ),
    )


def _merge_expert_artifacts(
    existing: tuple[ExpertArtifact, ...],
    incoming: tuple[ExpertArtifact, ...],
) -> tuple[ExpertArtifact, ...]:
    by_key: dict[str, ExpertArtifact] = {
        item.path or item.title: item for item in existing if item.path or item.title
    }
    for item in incoming:
        key = item.path or item.title
        if not key:
            continue
        by_key[key] = item
    return tuple(by_key.values())


def _follow_ups_from_delegation_result(
    mandate: str,
    source: ResidentObjective | None,
    result: ResidentExecutionResult,
    review: ResidentDelegationReview,
    *,
    limit: int,
    max_retry_depth: int,
) -> tuple[ResidentObjective, ...]:
    if source is None or limit <= 0:
        return ()
    follow_ups: list[ResidentObjective] = []
    dependencies = _delegation_follow_up_dependencies(source, review)
    for suggestion in result.follow_up_suggestions:
        if len(follow_ups) >= limit:
            break
        text = _compact_line(suggestion, limit=160)
        follow_ups.append(
            _objective_from_text(
                mandate,
                title=f"Follow up delegated result: {text}",
                source=f"{result.session_id}: {text}",
                kind=_kind_for_text(text),
                reasoning="Delegated execution produced a useful follow-up suggestion.",
                proof="Follow-up is resolved, retired, or converted into durable evidence.",
                dependencies=dependencies,
            )
        )
    if (
        review.decision == ResidentDelegationReviewDecision.RETRY.value
        and len(follow_ups) < limit
    ):
        follow_ups.append(
            _retry_or_review_failed_delegation_objective(
                mandate,
                source,
                result,
                review,
                max_retry_depth=max_retry_depth,
            )
        )
    if (
        review.decision == ResidentDelegationReviewDecision.NEEDS_FOLLOW_UP.value
        and review.missing_evidence
        and len(follow_ups) < limit
    ):
        missing = ", ".join(review.missing_evidence)
        follow_ups.append(
            _objective_from_text(
                mandate,
                title=f"Gather delegated evidence: {source.title}",
                source=f"{result.session_id}: missing {missing}",
                kind=ResidentObjectiveKind.VERIFICATION,
                reasoning="Delegated execution returned insufficient evidence for completion.",
                proof="Missing delegated evidence is found, recreated, or explicitly waived.",
                dependencies=dependencies,
            )
        )
    if result.blocked_reason and len(follow_ups) < limit:
        follow_ups.append(
            _objective_from_text(
                mandate,
                title=f"Resolve delegated blocker: {result.blocked_reason}",
                source=result.blocked_reason,
                kind=ResidentObjectiveKind.REVIEW,
                reasoning="Delegated execution reported a blocker that needs resident review.",
                proof="Blocker has a decision, workaround, or replacement objective.",
                dependencies=dependencies,
            )
        )
    return tuple(follow_ups)


def _delegation_follow_up_dependencies(
    source: ResidentObjective,
    review: ResidentDelegationReview,
) -> tuple[str, ...]:
    if review.decision == ResidentDelegationReviewDecision.COMPLETE.value:
        return (source.id,)
    return ()


def _retry_or_review_failed_delegation_objective(
    mandate: str,
    source: ResidentObjective,
    result: ResidentExecutionResult,
    review: ResidentDelegationReview,
    *,
    max_retry_depth: int,
) -> ResidentObjective:
    retry_depth = len(source.supersedes)
    if retry_depth >= max_retry_depth:
        return _objective_from_text(
            mandate,
            title=f"Review repeated delegation failure: {source.title}",
            source=f"{result.session_id}: {review.reason}",
            kind=ResidentObjectiveKind.REVIEW,
            reasoning=(
                "Delegated execution already used the configured retry depth; "
                "the resident should replan instead of retrying blindly."
            ),
            proof="A reviewed decision records whether to retry, replan, or ask the operator.",
            dependencies=(),
        )
    retry = _objective_from_text(
        mandate,
        title=f"Retry delegated result: {source.title}",
        source=f"{result.session_id}: {review.reason}",
        kind=ResidentObjectiveKind.REMOTE_EXECUTION,
        reasoning=(
            "Delegated execution failed or became unavailable; the resident should "
            "try one bounded replacement worker before escalating."
        ),
        proof="A retry delegation produces evidence, a reviewed blocker, or a replacement plan.",
        dependencies=(),
    )
    return retry.with_updates(supersedes=_merge_text(source.supersedes, (source.id,)))


def _delegation_next_action(
    created: list[ResidentDelegationRecord],
    observed: list[ResidentExecutionResult],
    questions: list[str],
) -> str:
    if questions:
        return f"Ask operator: {questions[0]}"
    if observed:
        return "Review absorbed delegated results and select the next portfolio objective."
    if created:
        return "Observe delegated sessions and absorb their outputs."
    return "No delegate-ready objective found."


def _delegation_report_had_activity(report: ResidentDelegationReport) -> bool:
    return bool(
        report.selected_objectives
        or report.created_delegations
        or report.observed_results
        or report.created_follow_up_objectives
        or report.operator_questions
    )


def _autonomy_attention_reason(report: ResidentDelegationReport) -> str:
    if report.selected_objectives:
        titles = ", ".join(item.title for item in report.selected_objectives[:2])
        return f"delegate-ready resident objective(s): {titles}"
    if report.operator_questions:
        return f"operator judgment needed: {report.operator_questions[0]}"
    if report.observed_results:
        return "delegated result(s) available for review"
    return "portfolio inspection found no delegate-ready work"


def _delegation_cycle_work_items(report: ResidentDelegationReport) -> tuple[str, ...]:
    items: list[str] = []
    items.extend(
        f"delegated {item.source_objective_id} via {item.backend_name}:{item.backend_session_id}"
        for item in report.created_delegations
    )
    items.extend(f"review {item.id}: {item.decision}" for item in report.reviews)
    items.extend(f"follow-up objective: {item.id}" for item in report.created_follow_up_objectives)
    return tuple(items)


def _delegation_cycle_findings(report: ResidentDelegationReport) -> tuple[str, ...]:
    findings: list[str] = []
    for result in report.observed_results:
        findings.append(f"{result.session_id}: {result.summary}")
        findings.extend(result.findings[:2])
    findings.extend(report.operator_questions[:2])
    return tuple(_compact_line(item, limit=220) for item in findings if item)


def _result_usage(results: tuple[ResidentExecutionResult, ...]) -> TokenUsage:
    usage = TokenUsage(input_tokens=0, output_tokens=0)
    for result in results:
        usage += result.usage.usage
    return usage


def _score_objective(
    objective: ResidentObjective,
    *,
    completed_ids: set[str],
    mandate: str,
) -> ResidentObjective:
    score = 10
    rationale: list[str] = []
    if objective.status in {
        ResidentObjectiveStatus.ACTIVE.value,
        ResidentObjectiveStatus.PAUSED.value,
    }:
        score += 25
        rationale.append("resume existing work")
    if objective.dependencies:
        if set(objective.dependencies).issubset(completed_ids):
            score += 20
            rationale.append("dependencies satisfied")
        else:
            score -= 80
            rationale.append("dependencies not ready")
    else:
        score += 10
        rationale.append("no dependency blocker")
    if objective.kind in {
        ResidentObjectiveKind.TOOL_BUILDING.value,
        ResidentObjectiveKind.IMPLEMENTATION.value,
        ResidentObjectiveKind.CONSOLIDATION.value,
    }:
        score += 20
        rationale.append("high leverage kind")
    if objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value:
        score -= 40
        rationale.append("operator input needed")
    if objective.risk_boundaries:
        score -= 20
        rationale.append("risk boundary present")
    if objective.proof_criteria:
        score += 10
        rationale.append("clear proof criteria")
    if _has_any(_objective_text(objective), ("gap", "missing", "blocked", "unavailable")):
        score += 20
        rationale.append("reduces a remembered gap")
    overlap = _mandate_overlap(mandate, objective.title + " " + objective.purpose)
    if overlap:
        score += min(20, overlap * 4)
        rationale.append("aligns with mandate language")
    band = "high" if score >= 60 else "medium" if score >= 35 else "low"
    return objective.with_updates(
        priority_score=score,
        priority_band=band,
        priority_rationale=", ".join(dict.fromkeys(rationale)),
    )


def _objective_from_text(
    mandate: str,
    *,
    title: str,
    source: str,
    kind: ResidentObjectiveKind,
    reasoning: str,
    proof: str,
    status: ResidentObjectiveStatus = ResidentObjectiveStatus.CANDIDATE,
    dependencies: tuple[str, ...] = (),
    pending_question: str = "",
) -> ResidentObjective:
    clean_title = _compact_line(title, limit=120)
    objective_id = _slug(clean_title) or "resident-objective"
    return ResidentObjective(
        id=objective_id,
        title=clean_title,
        purpose=f"Advance long-horizon resident work around: {_compact_line(source, limit=180)}",
        serves_mandate_because=(
            "The remembered evidence indicates this work may advance the mandate."
        ),
        expected_outcome=proof,
        proof_criteria=(proof,),
        kind=kind.value,
        dependencies=dependencies,
        required_capabilities=_capabilities_for_kind(kind),
        risk_boundaries=_risk_boundaries_for_text(source),
        budget_estimate="small",
        status=status.value,
        source_evidence=(source,),
        reasoning=reasoning,
        pending_question=pending_question,
    )


def _kind_for_text(text: str) -> ResidentObjectiveKind:
    lowered = text.casefold()
    if _has_any(lowered, ("tool", "workflow", "adapter", "backend", "capability")):
        return ResidentObjectiveKind.TOOL_BUILDING
    if _has_any(lowered, ("implement", "build", "prototype")):
        return ResidentObjectiveKind.IMPLEMENTATION
    if _has_any(lowered, ("verify", "test", "proof", "quality")):
        return ResidentObjectiveKind.VERIFICATION
    if _has_any(lowered, ("spec", "requirement", "design")):
        return ResidentObjectiveKind.SPECIFICATION
    if _has_any(lowered, ("review", "retro", "artifact")):
        return ResidentObjectiveKind.REVIEW
    if _has_any(lowered, ("creative", "explore", "idea")):
        return ResidentObjectiveKind.CREATIVE_EXPLORATION
    return ResidentObjectiveKind.RESEARCH


def _needs_human_answer(text: str) -> bool:
    lowered = text.casefold()
    return _has_any(lowered, ("operator", "human", "approval", "provide", "which ", "what "))


def _capabilities_for_kind(kind: ResidentObjectiveKind) -> tuple[str, ...]:
    if kind == ResidentObjectiveKind.TOOL_BUILDING:
        return ("tool_building",)
    if kind == ResidentObjectiveKind.REMOTE_EXECUTION:
        return ("remote_execution",)
    if kind == ResidentObjectiveKind.RESEARCH:
        return ("research",)
    if kind == ResidentObjectiveKind.IMPLEMENTATION:
        return ("code",)
    return ()


def _risk_boundaries_for_text(text: str) -> tuple[str, ...]:
    lowered = text.casefold()
    risks: list[str] = []
    if _has_any(lowered, ("spend", "purchase", "money", "paid")):
        risks.append("spending")
    if _has_any(lowered, ("physical", "machine", "hardware", "operate")):
        risks.append("physical_operation")
    if _has_any(lowered, ("send", "publish", "external", "email")):
        risks.append("external_side_effect")
    if _has_any(lowered, ("delete", "destroy", "remove")):
        risks.append("destructive_change")
    return tuple(risks)


def _proof_progress_from_wake(wake_run: WakefulResidentRun) -> tuple[str, ...]:
    progress: list[str] = []
    for cycle in wake_run.cycles:
        progress.append(f"wake cycle {cycle.cycle_number}: {cycle.decision.value}")
        progress.extend(cycle.finding_summaries[:2])
    return tuple(_compact_line(item, limit=220) for item in progress if item)


def _proof_satisfied(
    proof_progress: tuple[str, ...],
    artifact_links: tuple[str, ...],
    consolidation_links: tuple[str, ...],
) -> bool:
    return bool(proof_progress and (artifact_links or consolidation_links))


def _objective_mandate(mandate: str, objective: ResidentObjective) -> str:
    criteria = "\n".join(f"- {item}" for item in objective.proof_criteria)
    return (
        f"{mandate}\n\n"
        "Resident portfolio selected this long-horizon objective to advance now.\n"
        f"Objective: {objective.title}\n"
        f"Purpose: {objective.purpose}\n"
        f"Reason: {objective.reasoning or objective.priority_rationale}\n"
        f"Expected outcome: {objective.expected_outcome}\n"
        f"Proof criteria:\n{criteria or '- useful bounded evidence'}\n\n"
        "Advance one bounded, safe step. Persist artifacts and stop within budget."
    )


def _decision_entry(
    *,
    decision: ResidentPortfolioDecisionKind,
    reason: str,
    selected: tuple[ResidentObjective, ...],
) -> str:
    titles = ", ".join(objective.title for objective in selected) or "none"
    return f"{datetime.now(UTC).isoformat()} [{decision.value}] {reason}; selected: {titles}"


def _render_portfolio(portfolio: ResidentPortfolio) -> str:
    return (
        "# Resident Work Portfolio\n\n"
        f"- updated_at: {portfolio.updated_at.isoformat()}\n"
        f"- mandate: {portfolio.mandate}\n"
        f"- domain_model_ref: {portfolio.domain_model_ref}\n\n"
        "## Objectives\n\n"
        f"{_render_list(_objective_line(item) for item in portfolio.objectives)}\n\n"
        f"## Active Objectives\n\n{_render_status(portfolio, ResidentObjectiveStatus.ACTIVE)}\n\n"
        "## Candidate Objectives\n\n"
        f"{_render_status(portfolio, ResidentObjectiveStatus.CANDIDATE)}\n\n"
        f"## Paused Objectives\n\n{_render_status(portfolio, ResidentObjectiveStatus.PAUSED)}\n\n"
        f"## Blocked Objectives\n\n{_render_status(portfolio, ResidentObjectiveStatus.BLOCKED)}\n\n"
        "## Completed Objectives\n\n"
        f"{_render_status(portfolio, ResidentObjectiveStatus.COMPLETED)}\n\n"
        f"## Superseded Or Cancelled Objectives\n\n{_render_superseded_cancelled(portfolio)}\n\n"
        f"## Wake Records\n\n{_render_list(portfolio.wake_record_links)}\n\n"
        f"## Workstreams\n\n{_render_list(portfolio.workstream_links)}\n\n"
        f"## Artifacts\n\n{_render_list(portfolio.artifact_links)}\n\n"
        f"## Consolidations\n\n{_render_list(portfolio.consolidation_links)}\n\n"
        f"## Decision History\n\n{_render_list(portfolio.decision_history)}\n"
    )


def _render_objective(objective: ResidentObjective) -> str:
    advanced = objective.last_advanced_at.isoformat() if objective.last_advanced_at else ""
    reviewed = objective.last_reviewed_at.isoformat() if objective.last_reviewed_at else ""
    return (
        f"# {objective.title}\n\n"
        f"- id: {objective.id}\n"
        f"- status: {objective.status}\n"
        f"- kind: {objective.kind}\n"
        f"- priority_score: {objective.priority_score}\n"
        f"- priority_band: {objective.priority_band}\n"
        f"- budget_estimate: {objective.budget_estimate}\n"
        f"- pending_question: {objective.pending_question}\n"
        f"- superseded_by: {objective.superseded_by}\n"
        f"- created_at: {objective.created_at.isoformat()}\n"
        f"- updated_at: {objective.updated_at.isoformat()}\n"
        f"- last_advanced_at: {advanced}\n"
        f"- last_reviewed_at: {reviewed}\n\n"
        f"## Purpose\n\n{objective.purpose}\n\n"
        f"## Serves Mandate Because\n\n{objective.serves_mandate_because}\n\n"
        f"## Expected Outcome\n\n{objective.expected_outcome}\n\n"
        f"## Proof Criteria\n\n{_render_list(objective.proof_criteria)}\n\n"
        f"## Dependencies\n\n{_render_list(objective.dependencies)}\n\n"
        f"## Required Capabilities\n\n{_render_list(objective.required_capabilities)}\n\n"
        f"## Risk Boundaries\n\n{_render_list(objective.risk_boundaries)}\n\n"
        f"## Priority Rationale\n\n{objective.priority_rationale or 'none'}\n\n"
        f"## Source Evidence\n\n{_render_list(objective.source_evidence)}\n\n"
        f"## Reasoning\n\n{objective.reasoning or 'none'}\n\n"
        f"## Proof Progress\n\n{_render_list(objective.proof_progress)}\n\n"
        f"## Artifact Links\n\n{_render_list(objective.artifact_links)}\n\n"
        f"## Wake Links\n\n{_render_list(objective.wake_links)}\n\n"
        f"## Workstream Links\n\n{_render_list(objective.workstream_links)}\n\n"
        f"## Consolidation Links\n\n{_render_list(objective.consolidation_links)}\n\n"
        f"## Supersedes\n\n{_render_list(objective.supersedes)}\n"
    )


def _render_delegation(delegation: ResidentDelegationRecord) -> str:
    return (
        f"# Resident Delegation: {delegation.brief.objective_title}\n\n"
        f"- id: {delegation.id}\n"
        f"- source_objective_id: {delegation.source_objective_id}\n"
        f"- backend_session_id: {delegation.backend_session_id}\n"
        f"- backend_name: {delegation.backend_name}\n"
        f"- status: {delegation.status}\n"
        f"- created_at: {delegation.created_at.isoformat()}\n"
        f"- updated_at: {delegation.updated_at.isoformat()}\n\n"
        f"## Reason\n\n{delegation.reason}\n\n"
        f"## Risk Boundaries\n\n{_render_list(delegation.risk_boundaries)}\n\n"
        f"## Result Refs\n\n{_render_list(delegation.result_refs)}\n\n"
        f"## Follow-Up Objective Refs\n\n{_render_list(delegation.follow_up_objective_refs)}\n\n"
        f"## Worker Brief\n\n{render_worker_brief(delegation.brief)}"
    )


def _parse_portfolio(content: str, *, mandate: str) -> ResidentPortfolio:
    metadata = _metadata(content)
    return ResidentPortfolio(
        mandate=metadata.get("mandate") or mandate,
        domain_model_ref=metadata.get("domain_model_ref", ""),
        wake_record_links=tuple(_section_items(content, "Wake Records")),
        workstream_links=tuple(_section_items(content, "Workstreams")),
        artifact_links=tuple(_section_items(content, "Artifacts")),
        consolidation_links=tuple(_section_items(content, "Consolidations")),
        decision_history=tuple(_section_items(content, "Decision History")),
    )


def _parse_objective(content: str) -> ResidentObjective | None:
    metadata = _metadata(content)
    title = _title(content)
    objective_id = metadata.get("id") or _slug(title)
    if not title or not objective_id:
        return None
    return ResidentObjective(
        id=objective_id,
        title=title,
        purpose=_section(content, "Purpose"),
        serves_mandate_because=_section(content, "Serves Mandate Because"),
        expected_outcome=_section(content, "Expected Outcome"),
        proof_criteria=tuple(_section_items(content, "Proof Criteria")),
        kind=metadata.get("kind") or ResidentObjectiveKind.RESEARCH.value,
        dependencies=tuple(_section_items(content, "Dependencies")),
        required_capabilities=tuple(_section_items(content, "Required Capabilities")),
        risk_boundaries=tuple(_section_items(content, "Risk Boundaries")),
        budget_estimate=metadata.get("budget_estimate") or "small",
        priority_score=_int_value(metadata.get("priority_score")),
        priority_band=metadata.get("priority_band") or "normal",
        priority_rationale=_section(content, "Priority Rationale"),
        status=metadata.get("status") or ResidentObjectiveStatus.CANDIDATE.value,
        source_evidence=tuple(_section_items(content, "Source Evidence")),
        reasoning=_section(content, "Reasoning"),
        pending_question=metadata.get("pending_question") or "",
        proof_progress=tuple(_section_items(content, "Proof Progress")),
        artifact_links=tuple(_section_items(content, "Artifact Links")),
        wake_links=tuple(_section_items(content, "Wake Links")),
        workstream_links=tuple(_section_items(content, "Workstream Links")),
        consolidation_links=tuple(_section_items(content, "Consolidation Links")),
        supersedes=tuple(_section_items(content, "Supersedes")),
        superseded_by=metadata.get("superseded_by") or "",
    )


def _parse_delegation(content: str) -> ResidentDelegationRecord | None:
    metadata = _metadata(content)
    delegation_id = metadata.get("id") or _slug(_title(content))
    source_objective_id = metadata.get("source_objective_id", "")
    if not delegation_id or not source_objective_id:
        return None
    brief = _parse_worker_brief(_section_tail(content, "Worker Brief"))
    if brief is None:
        brief = ResidentWorkerBrief(
            id=f"brief-{delegation_id}",
            mandate="",
            objective_id=source_objective_id,
            objective_title=_title(content).removeprefix("Resident Delegation: ").strip(),
            desired_outcome="",
            proof_criteria=(),
        )
    return ResidentDelegationRecord(
        id=delegation_id,
        source_objective_id=source_objective_id,
        backend_session_id=metadata.get("backend_session_id", ""),
        backend_name=metadata.get("backend_name", ""),
        brief=brief,
        status=metadata.get("status") or ResidentDelegationStatus.LAUNCHED.value,
        reason=_section(content, "Reason"),
        risk_boundaries=tuple(_section_items(content, "Risk Boundaries")),
        result_refs=tuple(_section_items(content, "Result Refs")),
        follow_up_objective_refs=tuple(_section_items(content, "Follow-Up Objective Refs")),
        created_at=_datetime_value(metadata.get("created_at")),
        updated_at=_datetime_value(metadata.get("updated_at")),
    )


def _parse_worker_brief(content: str) -> ResidentWorkerBrief | None:
    metadata = _metadata(content)
    objective_id = metadata.get("objective_id", "")
    title = _title(content).removeprefix("Resident Worker Brief: ").strip()
    if not objective_id or not title:
        return None
    return ResidentWorkerBrief(
        id=metadata.get("brief_id") or f"brief-{objective_id}",
        mandate=_section(content, "Resident Mandate"),
        objective_id=objective_id,
        objective_title=title,
        desired_outcome=_section(content, "Desired Outcome"),
        proof_criteria=tuple(_section_items(content, "Proof Criteria")),
        evidence=tuple(_section_items(content, "Evidence")),
        artifact_links=tuple(_section_items(content, "Artifact Links")),
        constraints=tuple(_section_items(content, "Constraints")),
        risk_boundaries=tuple(_section_items(content, "Risk Boundaries")),
        expected_output_shape=_section(content, "Expected Output Shape"),
    )


def _steward_decision_entry(
    pass_number: int,
    action: ResidentPortfolioStewardActionKind,
    reason: str,
) -> str:
    return f"{datetime.now(UTC).isoformat()} [steward:{action.value}] pass {pass_number}: {reason}"


def _skipped_repair(
    finding: ResidentPortfolioValidationFinding,
    reason: str,
) -> ResidentPortfolioRepairRecord:
    return ResidentPortfolioRepairRecord(
        code=finding.code,
        objective_id=finding.objective_id,
        action="skip",
        reason=reason,
        ref=finding.ref,
    )


def _repair_skip_reason(finding: ResidentPortfolioValidationFinding) -> str:
    if finding.code == "completed_without_proof":
        return "cannot invent proof for a completed objective"
    if finding.code == "unstable_objective_id":
        return "backend cannot safely atomically rename objective and update all references"
    if finding.code == "missing_required_field":
        return "missing required semantics need resident or operator judgment"
    if finding.code == "invalid_status":
        return "invalid status needs review before the steward can choose a replacement"
    if finding.code == "implausible_portfolio_link":
        return "portfolio link target must be inspected before rewriting"
    if finding.code == "duplicate_objective_id":
        return "duplicate objective records need explicit merge review"
    return "no conservative repair is available"


def _follow_up_from_finding(
    mandate: str,
    objective: ResidentObjective,
    finding: str,
) -> ResidentObjective | None:
    text = _compact_line(finding, limit=180)
    lowered = text.casefold()
    if not _has_any(
        lowered,
        (
            "gap",
            "missing",
            "blocked",
            "partial",
            "failed",
            "opportunity",
            "question",
            "judgment",
            "approval",
        ),
    ):
        return None
    needs_operator = _has_any(lowered, ("question", "judgment", "approval", "operator", "human"))
    if needs_operator:
        return _objective_from_text(
            mandate,
            title=f"Ask operator: {text}",
            source=text,
            kind=ResidentObjectiveKind.OPERATOR_QUESTION,
            reasoning="Wake evidence indicates human judgment is needed.",
            proof="Operator answer is recorded and converted into policy or follow-up work.",
            status=ResidentObjectiveStatus.NEEDS_OPERATOR,
            dependencies=(objective.id,),
            pending_question=text,
        )
    if _has_any(lowered, ("gap", "missing", "capability", "tool", "workflow", "adapter")):
        return _objective_from_text(
            mandate,
            title=f"Discover capability path: {text}",
            source=text,
            kind=ResidentObjectiveKind.RESEARCH,
            reasoning="Wake evidence exposed a capability gap that needs bounded discovery.",
            proof="Capability discovery is persisted and converted into safe follow-up work.",
            dependencies=(objective.id,),
        )
    kind = (
        ResidentObjectiveKind.TOOL_BUILDING
        if _has_any(lowered, ("gap", "missing", "capability", "unavailable"))
        else ResidentObjectiveKind.REVIEW
        if _has_any(lowered, ("failed", "partial", "blocked"))
        else ResidentObjectiveKind.RESEARCH
    )
    return _objective_from_text(
        mandate,
        title=f"Follow up: {text}",
        source=text,
        kind=kind,
        reasoning="Wake evidence exposed new useful resident work.",
        proof="Follow-up is resolved, retired, or advanced into a durable artifact.",
        dependencies=(objective.id,),
    )


def _sum_budget(budgets: tuple[ResidentBudgetSnapshot, ...]) -> ResidentBudgetSnapshot:
    turns = sum(item.turns_used for item in budgets)
    usage = TokenUsage(input_tokens=0, output_tokens=0)
    for budget in budgets:
        usage += budget.usage
    return ResidentBudgetSnapshot(turns_used=turns, usage=usage)


def render_validation_report(report: ResidentPortfolioValidationReport) -> str:
    selected = (
        _preview_line(report.selected_objective)
        if report.selected_objective is not None
        else "none"
    )
    return (
        "# Resident Portfolio Validation Report\n\n"
        f"- verdict: {report.verdict}\n"
        f"- mutated_state: {str(report.mutated_state).lower()}\n"
        f"- selected_objective: {selected}\n\n"
        f"## Issues\n\n{_render_list(_finding_line(item) for item in report.issues)}\n\n"
        f"## Warnings\n\n{_render_list(_finding_line(item) for item in report.warnings)}\n\n"
        "## Objective Counts By Status\n\n"
        f"{_render_list(_count_line(item) for item in report.objective_counts_by_status)}\n\n"
        "## Eligible Objectives\n\n"
        f"{_render_list(_preview_line(item) for item in report.eligible_objectives)}\n\n"
        "## Blocked Objectives\n\n"
        f"{_render_list(_preview_line(item) for item in report.blocked_objectives)}\n\n"
        "## Operator Needed Objectives\n\n"
        f"{_render_list(_preview_line(item) for item in report.operator_needed_objectives)}\n\n"
        f"## Skipped Reasons\n\n{_render_list(report.skipped_reasons)}\n\n"
        f"## Dependency Graph\n\n{report.dependency_graph_summary}\n\n"
        f"## Hints\n\n{_render_list(report.stale_duplicate_superseded_hints)}\n\n"
        f"## Suggested Safe Next Action\n\n{report.suggested_safe_next_action}\n"
    )


def render_steward_report(run: ResidentPortfolioStewardRun) -> str:
    pass_blocks = []
    for report in run.passes:
        selected = (
            _preview_line(report.selected_objective)
            if report.selected_objective is not None
            else "none"
        )
        pass_blocks.append(
            f"## Pass {report.pass_number}\n\n"
            f"- validation_before: {report.validation_before.verdict}\n"
            f"- validation_after: {report.validation_after.verdict}\n"
            f"- action_taken: {report.action_taken.value}\n"
            f"- selected_objective: {selected}\n"
            f"- advanced_objective_id: {report.advanced_objective_id or 'none'}\n"
            f"- budget_turns: {report.budget.turns_used}\n"
            f"- persisted_refs: {', '.join(report.persisted_refs) or 'none'}\n"
            f"- final_suggested_next_action: {report.final_suggested_next_action}\n\n"
            "### Repairs Attempted\n\n"
            f"{_render_list(_repair_line(item) for item in report.repairs_attempted)}\n\n"
            "### Repairs Skipped\n\n"
            f"{_render_list(_repair_line(item) for item in report.repairs_skipped)}\n\n"
            "### Follow-Up Objectives\n\n"
            f"{_render_list(_objective_line(item) for item in report.new_follow_up_objectives)}\n\n"
            f"### Operator Questions\n\n{_render_list(report.operator_questions)}"
        )
    return (
        "# Resident Portfolio Steward Report\n\n"
        f"- final_action: {run.final_action.value}\n"
        f"- final_suggested_next_action: {run.final_suggested_next_action}\n"
        f"- total_passes: {len(run.passes)}\n"
        f"- budget_turns: {run.budget.turns_used}\n\n" + "\n\n".join(pass_blocks)
    )


def _repair_line(record: ResidentPortfolioRepairRecord) -> str:
    target = f" ({record.objective_id})" if record.objective_id else ""
    ref = f"; ref={record.ref}" if record.ref else ""
    change = f"; {record.before} -> {record.after}" if record.before or record.after else ""
    return f"{record.action}:{record.code}{target}: {record.reason}{change}{ref}"


def _validate_required_fields(
    objective: ResidentObjective,
    issues: list[ResidentPortfolioValidationFinding],
) -> None:
    required = {
        "id": objective.id,
        "title": objective.title,
        "purpose": objective.purpose,
        "serves_mandate_because": objective.serves_mandate_because,
        "expected_outcome": objective.expected_outcome,
        "kind": objective.kind,
        "status": objective.status,
    }
    for field_name, value in required.items():
        if not str(value).strip():
            issues.append(
                _finding(
                    "missing_required_field",
                    f"Missing required objective field: {field_name}",
                    objective_id=objective.id,
                )
            )
    if not objective.proof_criteria:
        issues.append(
            _finding(
                "missing_required_field",
                "Missing required objective field: proof_criteria",
                objective_id=objective.id,
            )
        )


def _validate_status(
    objective: ResidentObjective,
    issues: list[ResidentPortfolioValidationFinding],
) -> None:
    valid = {status.value for status in ResidentObjectiveStatus}
    if objective.status not in valid:
        issues.append(
            _finding(
                "invalid_status",
                f"Invalid objective status: {objective.status}",
                objective_id=objective.id,
            )
        )


def _validate_stable_id(
    objective: ResidentObjective,
    warnings: list[ResidentPortfolioValidationFinding],
) -> None:
    stable = _slug(objective.id)
    if not objective.id or stable != objective.id:
        warnings.append(
            _finding(
                "unstable_objective_id",
                "Objective id is not slug-stable.",
                objective_id=objective.id,
                severity=ResidentPortfolioValidationSeverity.WARNING,
            )
        )


def _validate_objective_state(
    objective: ResidentObjective,
    issues: list[ResidentPortfolioValidationFinding],
    warnings: list[ResidentPortfolioValidationFinding],
) -> None:
    if (
        objective.status == ResidentObjectiveStatus.COMPLETED.value
        and not objective.proof_progress
        and not objective.artifact_links
        and not objective.consolidation_links
    ):
        issues.append(
            _finding(
                "completed_without_proof",
                (
                    "Completed objective has no proof progress, artifact links, "
                    "or consolidation links."
                ),
                objective_id=objective.id,
            )
        )
    if objective.status in {
        ResidentObjectiveStatus.ACTIVE.value,
        ResidentObjectiveStatus.PAUSED.value,
    } and not (
        _has_meaningful_text(objective.reasoning)
        or _has_meaningful_text(objective.priority_rationale)
    ):
        warnings.append(
            _finding(
                "resume_reason_missing",
                "Active/paused objective lacks enough reason to resume.",
                objective_id=objective.id,
                severity=ResidentPortfolioValidationSeverity.WARNING,
            )
        )
    if (
        objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value
        and not objective.pending_question
    ):
        issues.append(
            _finding(
                "operator_question_missing",
                "needs_operator objective has no pending question.",
                objective_id=objective.id,
            )
        )
    if objective.status in {
        ResidentObjectiveStatus.SUPERSEDED.value,
        ResidentObjectiveStatus.CANCELLED.value,
    } and not (
        _has_meaningful_text(objective.reasoning)
        or _has_meaningful_text(objective.superseded_by)
        or _has_meaningful_items(objective.supersedes)
    ):
        warnings.append(
            _finding(
                "audit_context_missing",
                "Superseded/cancelled objective lacks audit context.",
                objective_id=objective.id,
                severity=ResidentPortfolioValidationSeverity.WARNING,
            )
        )


def _validate_portfolio_links(
    portfolio: ResidentPortfolio,
    warnings: list[ResidentPortfolioValidationFinding],
) -> None:
    expected = (
        ("wake_record", portfolio.wake_record_links, "resident/wakeful/cycles/"),
        ("workstream", portfolio.workstream_links, "resident/domain-expert/workstreams/"),
        ("artifact", portfolio.artifact_links, "resident/domain-expert/artifacts/"),
        (
            "consolidation",
            portfolio.consolidation_links,
            "resident/domain-expert/consolidations/",
        ),
    )
    for kind, refs, prefix in expected:
        for ref in refs:
            if not ref.startswith(prefix) or not ref.endswith(".md"):
                warnings.append(
                    _finding(
                        "implausible_portfolio_link",
                        f"Portfolio {kind} link has an unexpected shape: {ref}",
                        ref=ref,
                        severity=ResidentPortfolioValidationSeverity.WARNING,
                    )
                )


def _preview_objective(
    objective: ResidentObjective,
    all_objectives: tuple[ResidentObjective, ...],
    *,
    completed_ids: set[str],
) -> ResidentObjectiveDryRunPreview:
    known_ids = {item.id for item in all_objectives}
    return ResidentObjectiveDryRunPreview(
        objective_id=objective.id,
        title=objective.title,
        status=objective.status,
        priority_score=objective.priority_score,
        priority_rationale=objective.priority_rationale,
        dependency_readiness=_dependency_readiness(objective, known_ids, completed_ids),
        budget_notes=f"budget estimate: {objective.budget_estimate or 'unknown'}",
        risk_notes=", ".join(objective.risk_boundaries) if objective.risk_boundaries else "none",
    )


def _objective_by_id(
    objectives: tuple[ResidentObjective, ...],
    objective_id: str,
) -> ResidentObjective:
    for objective in objectives:
        if objective.id == objective_id:
            return objective
    return objectives[0]


def _objective_by_id_or_none(
    objectives: tuple[ResidentObjective, ...],
    objective_id: str,
) -> ResidentObjective | None:
    for objective in objectives:
        if objective.id == objective_id:
            return objective
    return None


def _dependency_readiness(
    objective: ResidentObjective,
    known_ids: set[str],
    completed_ids: set[str],
) -> str:
    if not objective.dependencies:
        return "ready: no dependencies"
    missing = tuple(item for item in objective.dependencies if item not in known_ids)
    if missing:
        return f"blocked: missing dependencies {', '.join(missing)}"
    incomplete = tuple(item for item in objective.dependencies if item not in completed_ids)
    if incomplete:
        return f"blocked: incomplete dependencies {', '.join(incomplete)}"
    return "ready: dependencies completed"


def _is_dependency_blocked(objective: ResidentObjective, completed_ids: set[str]) -> bool:
    return bool(objective.dependencies and not set(objective.dependencies).issubset(completed_ids))


def _is_blocked_objective(objective: ResidentObjective, completed_ids: set[str]) -> bool:
    return objective.status == ResidentObjectiveStatus.BLOCKED.value or _is_dependency_blocked(
        objective,
        completed_ids,
    )


def _dry_run_non_selection_reason(
    objective: ResidentObjective,
    *,
    known_ids: set[str],
    completed_ids: set[str],
    eligible_ids: set[str],
    selected_ids: set[str],
) -> str:
    reason = _skip_reason(objective, known_ids, completed_ids)
    if reason:
        return reason
    if objective.id in eligible_ids:
        selected = ", ".join(sorted(selected_ids)) or "none"
        return f"{objective.id} not selected: dry-run selection budget filled by {selected}"
    return f"{objective.id} skipped: not eligible for dry-run selection"


def _skip_reason(
    objective: ResidentObjective,
    known_ids: set[str],
    completed_ids: set[str],
) -> str:
    if objective.status in {
        ResidentObjectiveStatus.CANCELLED.value,
        ResidentObjectiveStatus.SUPERSEDED.value,
        ResidentObjectiveStatus.COMPLETED.value,
    }:
        return f"{objective.id} skipped: terminal status {objective.status}"
    if objective.status == ResidentObjectiveStatus.BLOCKED.value:
        return f"{objective.id} skipped: blocked"
    if objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value:
        return f"{objective.id} skipped: operator input needed"
    if objective.dependencies:
        missing = set(objective.dependencies) - known_ids
        if missing:
            return f"{objective.id} skipped: dependencies missing"
        if _is_dependency_blocked(objective, completed_ids):
            return f"{objective.id} skipped: dependencies incomplete"
    return ""


def _counts_by_status(
    objectives: tuple[ResidentObjective, ...],
) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for objective in objectives:
        counts[objective.status] = counts.get(objective.status, 0) + 1
    return tuple(sorted(counts.items()))


def _dependency_summary(objectives: tuple[ResidentObjective, ...]) -> str:
    total_edges = sum(len(objective.dependencies) for objective in objectives)
    known = {objective.id for objective in objectives}
    missing = tuple(
        dependency
        for objective in objectives
        for dependency in objective.dependencies
        if dependency not in known
    )
    if missing:
        return (
            f"{len(objectives)} objectives, {total_edges} dependencies, "
            f"missing: {', '.join(missing)}"
        )
    return f"{len(objectives)} objectives, {total_edges} dependencies, all known"


def _hints(
    objectives: tuple[ResidentObjective, ...],
    issues: list[ResidentPortfolioValidationFinding],
    warnings: list[ResidentPortfolioValidationFinding],
) -> tuple[str, ...]:
    hints: list[str] = []
    if any(item.code == "duplicate_objective_id" for item in issues):
        hints.append("duplicate objective ids should be merged or superseded")
    stale = tuple(
        objective.id
        for objective in objectives
        if objective.status
        in {ResidentObjectiveStatus.ACTIVE.value, ResidentObjectiveStatus.PAUSED.value}
        and objective.last_advanced_at is None
    )
    if stale:
        hints.append(f"objectives may need review before resume: {', '.join(stale[:5])}")
    if any(item.code == "audit_context_missing" for item in warnings):
        hints.append("terminal objectives should preserve audit context")
    superseded = tuple(
        objective.id
        for objective in objectives
        if objective.status == ResidentObjectiveStatus.SUPERSEDED.value
    )
    if superseded:
        hints.append(f"superseded objectives present: {', '.join(superseded[:5])}")
    return tuple(hints)


def _suggested_action(
    verdict: str,
    selected: ResidentObjectiveDryRunPreview | None,
) -> str:
    if verdict == "invalid":
        return "Fix portfolio validation issues before advancing resident work."
    if selected is None:
        return "No eligible objective; sleep or ask for operator input."
    return f"Dry-run would advance: {selected.title}"


def _finding(
    code: str,
    message: str,
    *,
    objective_id: str = "",
    ref: str = "",
    severity: ResidentPortfolioValidationSeverity = ResidentPortfolioValidationSeverity.ISSUE,
) -> ResidentPortfolioValidationFinding:
    return ResidentPortfolioValidationFinding(
        severity=severity,
        code=code,
        message=message,
        objective_id=objective_id,
        ref=ref,
    )


def _finding_line(finding: ResidentPortfolioValidationFinding) -> str:
    target = finding.objective_id or finding.ref
    suffix = f" ({target})" if target else ""
    return f"[{finding.severity.value}] {finding.code}: {finding.message}{suffix}"


def _count_line(item: tuple[str, int]) -> str:
    status, count = item
    return f"{status}: {count}"


def _preview_line(preview: ResidentObjectiveDryRunPreview) -> str:
    return (
        f"{preview.objective_id}: {preview.title} [{preview.status}] "
        f"priority={preview.priority_score}; {preview.dependency_readiness}; "
        f"{preview.budget_notes}; risks={preview.risk_notes}; "
        f"reason={preview.priority_rationale or 'none'}"
    )


def _render_status(portfolio: ResidentPortfolio, status: ResidentObjectiveStatus) -> str:
    return _render_list(
        _objective_line(item) for item in portfolio.objectives if item.status == status.value
    )


def _render_superseded_cancelled(portfolio: ResidentPortfolio) -> str:
    return _render_list(
        _objective_line(item)
        for item in portfolio.objectives
        if item.status
        in {ResidentObjectiveStatus.SUPERSEDED.value, ResidentObjectiveStatus.CANCELLED.value}
    )


def _objective_line(objective: ResidentObjective) -> str:
    return (
        f"{objective.id}: {objective.title} [{objective.status}] "
        f"priority={objective.priority_score} proof={len(objective.proof_criteria)}"
    )


def _metadata(content: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in content.splitlines():
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        data[key.strip()] = value.strip()
    return data


def _title(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return ""


def _section(content: str, name: str) -> str:
    lines = _section_lines(content, name)
    return "\n".join(line for line in lines if not line.startswith("- ")).strip()


def _section_items(content: str, name: str) -> list[str]:
    items: list[str] = []
    for line in _section_lines(content, name):
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if value and value != "none":
                items.append(value)
    return items


def _section_lines(content: str, name: str) -> list[str]:
    wanted = f"## {name}".casefold()
    lines = content.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if line.strip().casefold() == wanted:
            start = idx + 1
            break
    if start < 0:
        return []
    collected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.strip():
            collected.append(line)
    return collected


def _section_tail(content: str, name: str) -> str:
    wanted = f"## {name}".casefold()
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if line.strip().casefold() == wanted:
            return "\n".join(lines[idx + 1 :]).strip()
    return ""


def _render_list(items: Any) -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    if not values:
        return "- none"
    return "\n".join(f"- {item}" for item in values)


def _merge_text(*groups: tuple[str, ...], limit: int = 40) -> tuple[str, ...]:
    merged: list[str] = []
    for group in groups:
        for item in group:
            value = str(item).strip()
            if value and value not in merged:
                merged.append(value)
            if len(merged) >= limit:
                return tuple(merged)
    return tuple(merged)


def _status_rank(status: str) -> int:
    ranks = {
        ResidentObjectiveStatus.CANCELLED.value: 0,
        ResidentObjectiveStatus.SUPERSEDED.value: 1,
        ResidentObjectiveStatus.CANDIDATE.value: 2,
        ResidentObjectiveStatus.BLOCKED.value: 3,
        ResidentObjectiveStatus.NEEDS_OPERATOR.value: 4,
        ResidentObjectiveStatus.PAUSED.value: 5,
        ResidentObjectiveStatus.ACTIVE.value: 6,
        ResidentObjectiveStatus.COMPLETED.value: 7,
    }
    return ranks.get(status, 2)


def _objective_text(objective: ResidentObjective) -> str:
    return " ".join(
        (
            objective.title,
            objective.purpose,
            objective.reasoning,
            " ".join(objective.source_evidence),
        )
    ).casefold()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _has_meaningful_text(value: str) -> bool:
    normalized = str(value).strip()
    return bool(normalized) and normalized.casefold() != "none"


def _has_meaningful_items(values: tuple[str, ...]) -> bool:
    return any(_has_meaningful_text(value) for value in values)


def _mandate_overlap(mandate: str, text: str) -> int:
    mandate_words = {
        word
        for word in _slug(mandate).split("-")
        if len(word) > 4 and word not in {"should", "without"}
    }
    text_words = set(_slug(text).split("-"))
    return len(mandate_words & text_words)


def _basename(ref: str) -> str:
    return Path(ref).name or ref


def _int_value(value: str | None) -> int:
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0


def _datetime_value(value: str | None) -> datetime:
    try:
        return datetime.fromisoformat(str(value or ""))
    except ValueError:
        return datetime.now(UTC)
