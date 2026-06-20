"""Resident long-horizon work management V0."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ravn.domain.resident_continuation import ResidentBudgetLimits
from ravn.domain.resident_expert import (
    ResidentDomainExpertMemoryPort,
    ResidentDomainModel,
    ResidentWorkstream,
    ResidentWorkstreamStatus,
)
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentPortfolioDecisionKind,
    ResidentPortfolioRun,
    ResidentWorkItemBackend,
)
from ravn.domain.wakeful_resident import WakefulResidentRun
from ravn.ports.mimir import MimirPort
from ravn.resident_continuation import ResidentRunBudget, _compact_line, _slug
from ravn.wakeful_resident import WakefulResidentMemoryPort

_PORTFOLIO_PATH = "resident/portfolio/portfolio.md"
_OBJECTIVE_PREFIX = "resident/portfolio/objectives"
_DECISION_PREFIX = "resident/portfolio/decisions"
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
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        rel = Path(_DECISION_PREFIX) / f"{stamp}.md"
        return self._write(rel, f"# Resident Portfolio Decision\n\n{entry}\n")

    async def list_refs(self, prefix: str) -> list[str]:
        base = self._root / prefix
        if not base.exists():
            return []
        return sorted(str(path.relative_to(self._root)) for path in base.glob("*.md"))

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
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = f"{_DECISION_PREFIX}/{stamp}.md"
        await self._mimir.upsert_page(path, f"# Resident Portfolio Decision\n\n{entry}\n")
        return path

    async def list_refs(self, prefix: str) -> list[str]:
        pages = await self._mimir.list_pages(prefix=prefix)
        return sorted(getattr(page, "path", "") for page in pages if getattr(page, "path", ""))


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
            portfolio = portfolio.with_objectives(tuple(updated_by_id.values()))
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
            decision_history=_merge_text(portfolio.decision_history, (decision_entry,), limit=20),
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
