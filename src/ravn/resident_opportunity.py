"""Resident opportunity generation runtime."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ravn.domain.resident_expert import ResidentDomainExpertMemoryPort, ResidentDomainModel
from ravn.domain.resident_opportunity import (
    ResidentOpportunityCandidate,
    ResidentOpportunityReport,
    ResidentOpportunityScore,
    ResidentOpportunitySignal,
    ResidentOpportunitySourcePort,
    ResidentOpportunityStage,
)
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentWorkItemBackend,
)
from ravn.ports.mimir import MimirPort
from ravn.resident_continuation import _compact_line, _slug
from ravn.resident_portfolio import (
    MimirResidentWorkItemBackend,
    _merge_text,
    _render_list,
    merge_objectives,
)

_OPPORTUNITY_PREFIX = "resident/opportunities"
_OPPORTUNITY_REPORT_PREFIX = "resident/opportunity-reports"


class ResidentOpportunityBackend(Protocol):
    """Persistence boundary for resident opportunity artifacts."""

    async def list_opportunities(self, mandate: str) -> list[ResidentOpportunityCandidate]:
        """Return previously persisted resident opportunities."""

    async def write_opportunity(self, opportunity: ResidentOpportunityCandidate) -> str:
        """Persist one opportunity artifact and return its reference."""

    async def write_opportunity_report(self, report: ResidentOpportunityReport) -> str:
        """Persist one opportunity-generation report and return its reference."""


@dataclass(frozen=True)
class ResidentOpportunityConfig:
    """Configurable bounds and scoring constants for one opportunity pass."""

    max_signals: int = 8
    max_candidates: int = 6
    max_selected: int = 2
    min_total_score: int = 18
    score_max: int = 10
    score_mid: int = 5
    evidence_score_step: int = 2
    outcome_score_step: int = 2
    signal_score_step: int = 1
    risk_penalty: int = 3
    cost_penalty: int = 2
    duplicate_penalty: int = 5
    rationale_outcome_limit: int = 3
    stop_words: tuple[str, ...] = (
        "about",
        "after",
        "and",
        "before",
        "better",
        "company",
        "domain",
        "from",
        "have",
        "into",
        "more",
        "resident",
        "sells",
        "should",
        "small",
        "that",
        "this",
        "what",
        "with",
        "work",
    )


class ResidentOpportunityRuntime:
    """Generate evidence-backed resident opportunities and portfolio work."""

    def __init__(
        self,
        *,
        backend: ResidentWorkItemBackend,
        opportunity_backend: ResidentOpportunityBackend,
        sources: tuple[ResidentOpportunitySourcePort, ...],
        expert_memory: ResidentDomainExpertMemoryPort | None = None,
        config: ResidentOpportunityConfig | None = None,
    ) -> None:
        self._backend = backend
        self._opportunities = opportunity_backend
        self._sources = sources
        self._expert_memory = expert_memory
        self._config = config or ResidentOpportunityConfig()

    async def run(self, mandate: str) -> ResidentOpportunityReport:
        domain_model = await self._read_domain_model(mandate)
        objectives = tuple(await self._backend.list_objectives(mandate))
        prior = tuple(await self._opportunities.list_opportunities(mandate))
        signals = await self._collect_signals(mandate, domain_model, objectives)
        candidates = _derive_opportunities(
            mandate=mandate,
            domain_model=domain_model,
            signals=signals,
            objectives=objectives,
            prior_opportunities=prior,
            config=self._config,
        )
        selected, suppressed, duplicate_notes = _select_opportunities(
            candidates,
            objectives=objectives,
            prior_opportunities=prior,
            config=self._config,
        )
        created_objectives = tuple(_objective_from_opportunity(mandate, item) for item in selected)

        persisted_refs: list[str] = []
        for opportunity in (*selected, *suppressed):
            persisted_refs.append(await self._opportunities.write_opportunity(opportunity))
        for objective in created_objectives:
            persisted_refs.append(await self._backend.write_objective(objective))

        if created_objectives:
            created_ids = {objective.id for objective in created_objectives}
            merged_objectives = (
                tuple(item for item in objectives if item.id not in created_ids)
                + created_objectives
            )
            portfolio_ref = await self._persist_portfolio(
                mandate,
                merged_objectives,
            )
            persisted_refs.append(portfolio_ref)

        report = ResidentOpportunityReport(
            mandate=mandate,
            signals=signals,
            candidates=candidates,
            selected_opportunities=selected,
            suppressed_opportunities=suppressed,
            created_objectives=created_objectives,
            persisted_refs=tuple(persisted_refs),
            duplicate_notes=duplicate_notes,
            budget_notes=(
                f"signals={len(signals)} candidates={len(candidates)} "
                f"selected={len(selected)} suppressed={len(suppressed)}"
            ),
            final_suggested_next_action=_final_next_action(selected, suppressed),
        )
        persisted_refs.append(await self._opportunities.write_opportunity_report(report))
        return ResidentOpportunityReport(
            mandate=report.mandate,
            signals=report.signals,
            candidates=report.candidates,
            selected_opportunities=report.selected_opportunities,
            suppressed_opportunities=report.suppressed_opportunities,
            created_objectives=report.created_objectives,
            persisted_refs=tuple(persisted_refs),
            duplicate_notes=report.duplicate_notes,
            budget_notes=report.budget_notes,
            final_suggested_next_action=report.final_suggested_next_action,
        )

    async def _read_domain_model(self, mandate: str) -> ResidentDomainModel | None:
        if self._expert_memory is None:
            return None
        return await self._expert_memory.read_domain_model(mandate)

    async def _collect_signals(
        self,
        mandate: str,
        domain_model: ResidentDomainModel | None,
        objectives: tuple[ResidentObjective, ...],
    ) -> tuple[ResidentOpportunitySignal, ...]:
        signals: list[ResidentOpportunitySignal] = []
        for source in self._sources:
            if len(signals) >= self._config.max_signals:
                break
            remaining = self._config.max_signals - len(signals)
            collected = await source.collect(
                mandate=mandate,
                domain_model=domain_model,
                objectives=objectives,
                limit=remaining,
            )
            signals.extend(collected[:remaining])
        if domain_model is not None and len(signals) < self._config.max_signals:
            signals.extend(_signals_from_domain_model(domain_model, self._config))
        return tuple(signals[: self._config.max_signals])

    async def _persist_portfolio(
        self,
        mandate: str,
        objectives: tuple[ResidentObjective, ...],
    ) -> str:
        stored = await self._backend.read_portfolio(mandate)
        portfolio = stored or ResidentPortfolio(mandate=mandate)
        merged = merge_objectives(objectives)
        for objective in merged:
            await self._backend.write_objective(objective)
        return await self._backend.write_portfolio(
            portfolio.with_objectives(
                merged,
                decision_history=_merge_text(
                    portfolio.decision_history,
                    (
                        f"{datetime.now(UTC).isoformat()} "
                        "[opportunity_generation] selected evidence-backed opportunities",
                    ),
                    limit=40,
                ),
            )
        )


class LocalResidentOpportunityBackend(ResidentOpportunityBackend):
    """Filesystem-backed resident opportunity persistence."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    async def list_opportunities(self, mandate: str) -> list[ResidentOpportunityCandidate]:
        base = self._root / _OPPORTUNITY_PREFIX
        if not base.exists():
            return []
        opportunities: list[ResidentOpportunityCandidate] = []
        for path in sorted(base.glob("*.md")):
            parsed = parse_opportunity(path.read_text(encoding="utf-8"))
            if parsed is not None:
                opportunities.append(parsed)
        return opportunities

    async def write_opportunity(self, opportunity: ResidentOpportunityCandidate) -> str:
        rel = Path(_OPPORTUNITY_PREFIX) / f"{opportunity.id}.md"
        return self._write(rel, render_opportunity(opportunity))

    async def write_opportunity_report(self, report: ResidentOpportunityReport) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        rel = Path(_OPPORTUNITY_REPORT_PREFIX) / f"{stamp}.md"
        return self._write(rel, render_opportunity_report(report))

    def _write(self, rel: Path, content: str) -> str:
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(rel)


class MimirResidentOpportunityBackend(ResidentOpportunityBackend):
    """Mimir-backed resident opportunity persistence."""

    def __init__(self, mimir: MimirPort) -> None:
        self._mimir = mimir

    async def list_opportunities(self, mandate: str) -> list[ResidentOpportunityCandidate]:
        metas = await self._mimir.list_pages(prefix=_OPPORTUNITY_PREFIX)
        opportunities: list[ResidentOpportunityCandidate] = []
        for meta in metas:
            try:
                content = await self._mimir.read_page(meta.path)
            except FileNotFoundError:
                continue
            parsed = parse_opportunity(content)
            if parsed is not None:
                opportunities.append(parsed)
        return opportunities

    async def write_opportunity(self, opportunity: ResidentOpportunityCandidate) -> str:
        path = f"{_OPPORTUNITY_PREFIX}/{opportunity.id}.md"
        await self._mimir.upsert_page(path, render_opportunity(opportunity))
        return path

    async def write_opportunity_report(self, report: ResidentOpportunityReport) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = f"{_OPPORTUNITY_REPORT_PREFIX}/{stamp}.md"
        await self._mimir.upsert_page(path, render_opportunity_report(report))
        return path


def build_mimir_opportunity_runtime(
    *,
    mimir: MimirPort,
    sources: tuple[ResidentOpportunitySourcePort, ...],
    expert_memory: ResidentDomainExpertMemoryPort | None = None,
    config: ResidentOpportunityConfig | None = None,
) -> ResidentOpportunityRuntime:
    """Build a resident opportunity runtime backed by the existing Mimir store."""

    return ResidentOpportunityRuntime(
        backend=MimirResidentWorkItemBackend(mimir),
        opportunity_backend=MimirResidentOpportunityBackend(mimir),
        sources=sources,
        expert_memory=expert_memory,
        config=config,
    )


def render_opportunity(opportunity: ResidentOpportunityCandidate) -> str:
    score = opportunity.score
    return (
        f"# {opportunity.title}\n\n"
        f"- id: {opportunity.id}\n"
        f"- stage: {opportunity.stage}\n"
        f"- duplicate_key: {opportunity.duplicate_key}\n"
        f"- expected_value: {score.expected_value}\n"
        f"- risk: {score.risk}\n"
        f"- cost: {score.cost}\n"
        f"- novelty: {score.novelty}\n"
        f"- feasibility: {score.feasibility}\n"
        f"- operator_alignment: {score.operator_alignment}\n"
        f"- total_score: {score.total}\n"
        f"- operator_question: {opportunity.operator_question}\n"
        f"- created_at: {opportunity.created_at.isoformat()}\n\n"
        f"## Summary\n\n{opportunity.summary}\n\n"
        f"## Rationale\n\n{opportunity.rationale}\n\n"
        f"## Evidence\n\n{_render_list(opportunity.evidence)}\n\n"
        f"## Assumptions\n\n{_render_list(opportunity.assumptions)}\n\n"
        f"## Risks\n\n{_render_list(opportunity.risks)}\n\n"
        f"## Safe Next Experiment\n\n{opportunity.safe_next_experiment}\n\n"
        f"## Source Signal IDs\n\n{_render_list(opportunity.source_signal_ids)}\n"
    )


def render_opportunity_report(report: ResidentOpportunityReport) -> str:
    return (
        "# Resident Opportunity Generation Report\n\n"
        f"- mandate: {report.mandate}\n"
        f"- signal_count: {len(report.signals)}\n"
        f"- candidate_count: {len(report.candidates)}\n"
        f"- selected_count: {len(report.selected_opportunities)}\n"
        f"- suppressed_count: {len(report.suppressed_opportunities)}\n\n"
        f"## Selected Opportunities\n\n"
        f"{_render_list(_opportunity_line(item) for item in report.selected_opportunities)}\n\n"
        f"## Suppressed Opportunities\n\n"
        f"{_render_list(_opportunity_line(item) for item in report.suppressed_opportunities)}\n\n"
        f"## Duplicate Notes\n\n{_render_list(report.duplicate_notes)}\n\n"
        "## Created Objectives\n\n"
        f"{_render_list(item.id for item in report.created_objectives)}\n\n"
        f"## Evidence Signals\n\n{_render_list(_signal_line(item) for item in report.signals)}\n\n"
        f"## Budget Notes\n\n{report.budget_notes}\n\n"
        f"## Final Suggested Next Action\n\n{report.final_suggested_next_action}\n"
    )


def parse_opportunity(content: str) -> ResidentOpportunityCandidate | None:
    metadata = _metadata(content)
    title = _title(content)
    opportunity_id = metadata.get("id") or _slug(title)
    duplicate_key = metadata.get("duplicate_key") or _dedupe_key(title)
    if not title or not opportunity_id:
        return None
    score = ResidentOpportunityScore(
        expected_value=_int_value(metadata.get("expected_value")),
        risk=_int_value(metadata.get("risk")),
        cost=_int_value(metadata.get("cost")),
        novelty=_int_value(metadata.get("novelty")),
        feasibility=_int_value(metadata.get("feasibility")),
        operator_alignment=_int_value(metadata.get("operator_alignment")),
        total=_int_value(metadata.get("total_score")),
    )
    return ResidentOpportunityCandidate(
        id=opportunity_id,
        title=title,
        summary=_section(content, "Summary"),
        stage=metadata.get("stage") or ResidentOpportunityStage.IDEA.value,
        rationale=_section(content, "Rationale"),
        evidence=tuple(_section_items(content, "Evidence")),
        assumptions=tuple(_section_items(content, "Assumptions")),
        risks=tuple(_section_items(content, "Risks")),
        safe_next_experiment=_section(content, "Safe Next Experiment"),
        score=score,
        duplicate_key=duplicate_key,
        source_signal_ids=tuple(_section_items(content, "Source Signal IDs")),
        operator_question=metadata.get("operator_question") or "",
    )


def _derive_opportunities(
    *,
    mandate: str,
    domain_model: ResidentDomainModel | None,
    signals: tuple[ResidentOpportunitySignal, ...],
    objectives: tuple[ResidentObjective, ...],
    prior_opportunities: tuple[ResidentOpportunityCandidate, ...],
    config: ResidentOpportunityConfig,
) -> tuple[ResidentOpportunityCandidate, ...]:
    grouped = _group_signals(signals, config)
    candidates: list[ResidentOpportunityCandidate] = []
    for theme, theme_signals in grouped:
        evidence = tuple(_signal_line(item) for item in theme_signals)
        outcomes = _merge_signal_items(theme_signals, "outcomes")
        risks = _risks_for_theme(theme, mandate, theme_signals)
        score = _score_opportunity(
            theme=theme,
            evidence=evidence,
            outcomes=outcomes,
            risks=risks,
            prior_opportunities=prior_opportunities,
            objectives=objectives,
            config=config,
        )
        title = _compact_line(f"Explore {theme} as a resident opportunity", limit=120)
        safe_experiment = (
            "Run a read-only evidence review, write a short experiment plan, "
            "and ask before spending money or operating external systems."
        )
        candidate = ResidentOpportunityCandidate(
            id=_slug(f"opportunity-{theme}") or "resident-opportunity",
            title=title,
            summary=_summary_for_theme(theme, theme_signals),
            stage=ResidentOpportunityStage.HYPOTHESIS.value,
            rationale=_rationale_for_theme(theme, outcomes, domain_model, config),
            evidence=evidence,
            assumptions=_assumptions_for_theme(theme, theme_signals),
            risks=risks,
            safe_next_experiment=safe_experiment,
            score=score,
            duplicate_key=_dedupe_key(theme),
            source_signal_ids=tuple(item.id for item in theme_signals),
            operator_question=_operator_question_for_risks(theme, risks),
        )
        candidates.append(candidate)
    candidates.sort(key=lambda item: (-item.score.total, item.duplicate_key))
    return tuple(candidates[: config.max_candidates])


def _select_opportunities(
    candidates: tuple[ResidentOpportunityCandidate, ...],
    *,
    objectives: tuple[ResidentObjective, ...],
    prior_opportunities: tuple[ResidentOpportunityCandidate, ...],
    config: ResidentOpportunityConfig,
) -> tuple[
    tuple[ResidentOpportunityCandidate, ...],
    tuple[ResidentOpportunityCandidate, ...],
    tuple[str, ...],
]:
    selected: list[ResidentOpportunityCandidate] = []
    suppressed: list[ResidentOpportunityCandidate] = []
    duplicate_notes: list[str] = []
    duplicate_keys, duplicate_evidence = _known_duplicate_state(objectives, prior_opportunities)
    for candidate in candidates:
        if candidate.duplicate_key in duplicate_keys:
            suppressed_candidate = candidate.with_stage(ResidentOpportunityStage.SUPPRESSED.value)
            suppressed.append(suppressed_candidate)
            duplicate_notes.append(
                f"suppressed duplicate opportunity {candidate.duplicate_key}: {candidate.title}"
            )
            continue
        candidate_evidence = _evidence_keys(candidate.evidence)
        if candidate_evidence & duplicate_evidence:
            suppressed_candidate = candidate.with_stage(ResidentOpportunityStage.SUPPRESSED.value)
            suppressed.append(suppressed_candidate)
            duplicate_notes.append(
                f"suppressed repeated evidence for {candidate.duplicate_key}: "
                f"{', '.join(sorted(candidate_evidence & duplicate_evidence))}"
            )
            continue
        if candidate.score.total < config.min_total_score:
            suppressed.append(candidate.with_stage(ResidentOpportunityStage.SUPPRESSED.value))
            duplicate_notes.append(
                f"suppressed low-scoring opportunity {candidate.duplicate_key}: "
                f"{candidate.score.total} < {config.min_total_score}"
            )
            continue
        if len(selected) >= config.max_selected:
            suppressed.append(candidate.with_stage(ResidentOpportunityStage.SUPPRESSED.value))
            continue
        selected_candidate = candidate.with_stage(ResidentOpportunityStage.EXPERIMENT.value)
        selected.append(selected_candidate)
        duplicate_keys.add(selected_candidate.duplicate_key)
        duplicate_evidence.update(candidate_evidence)
    return tuple(selected), tuple(suppressed), tuple(duplicate_notes)


def _objective_from_opportunity(
    mandate: str,
    opportunity: ResidentOpportunityCandidate,
) -> ResidentObjective:
    kind = (
        ResidentObjectiveKind.OPERATOR_QUESTION
        if opportunity.operator_question
        else ResidentObjectiveKind.CREATIVE_EXPLORATION
    )
    status = (
        ResidentObjectiveStatus.NEEDS_OPERATOR
        if opportunity.operator_question
        else ResidentObjectiveStatus.CANDIDATE
    )
    return ResidentObjective(
        id=_slug(f"opportunity-work-{opportunity.duplicate_key}") or opportunity.id,
        title=_compact_line(f"Run safe experiment: {opportunity.title}", limit=120),
        purpose=opportunity.safe_next_experiment,
        serves_mandate_because=(
            "The resident inferred this opportunity from remembered evidence and research "
            "without the operator providing a task."
        ),
        expected_outcome="A small evidence-backed experiment plan, artifact, or operator decision.",
        proof_criteria=(
            "The opportunity evidence, assumptions, risks, and safe next experiment are recorded.",
            "The next action avoids spend, physical operation, and external side effects "
            "without approval.",
        ),
        kind=kind.value,
        risk_boundaries=opportunity.risks,
        budget_estimate="small",
        priority_score=opportunity.score.total,
        priority_band="high" if not opportunity.operator_question else "operator",
        priority_rationale=opportunity.rationale,
        status=status.value,
        source_evidence=opportunity.evidence,
        reasoning=(
            f"Opportunity score {opportunity.score.total}; duplicate key "
            f"{opportunity.duplicate_key}; safe experiment selected."
        ),
        pending_question=opportunity.operator_question,
        artifact_links=(f"{_OPPORTUNITY_PREFIX}/{opportunity.id}.md",),
    )


def _signals_from_domain_model(
    model: ResidentDomainModel,
    config: ResidentOpportunityConfig,
) -> tuple[ResidentOpportunitySignal, ...]:
    signals: list[ResidentOpportunitySignal] = []
    for index, item in enumerate(model.opportunities):
        signals.append(
            ResidentOpportunitySignal(
                id=f"domain-opportunity-{index}",
                source="resident_domain_model",
                kind="memory",
                summary=item,
                evidence_ref="resident/domain-expert/domain-model.md",
                themes=tuple(_themes_from_text(item, config)),
                outcomes=tuple(model.known_facts[: config.score_mid]),
            )
        )
    for index, item in enumerate((*model.failure_notes, *model.capability_gaps)):
        signals.append(
            ResidentOpportunitySignal(
                id=f"domain-gap-{index}",
                source="resident_domain_model",
                kind="memory_gap",
                summary=item,
                evidence_ref="resident/domain-expert/domain-model.md",
                themes=tuple(_themes_from_text(item, config)),
                outcomes=tuple(model.open_threads[: config.score_mid]),
            )
        )
    return tuple(signals[: config.max_signals])


def _group_signals(
    signals: tuple[ResidentOpportunitySignal, ...],
    config: ResidentOpportunityConfig,
) -> tuple[tuple[str, tuple[ResidentOpportunitySignal, ...]], ...]:
    groups: dict[str, list[ResidentOpportunitySignal]] = {}
    for signal in signals:
        themes = signal.themes or tuple(_themes_from_text(signal.summary, config))
        for theme in themes[: config.score_mid]:
            key = _compact_line(theme, limit=80)
            if key:
                groups.setdefault(key, []).append(signal)
    return tuple((theme, tuple(items)) for theme, items in groups.items())


def _score_opportunity(
    *,
    theme: str,
    evidence: tuple[str, ...],
    outcomes: tuple[str, ...],
    risks: tuple[str, ...],
    prior_opportunities: tuple[ResidentOpportunityCandidate, ...],
    objectives: tuple[ResidentObjective, ...],
    config: ResidentOpportunityConfig,
) -> ResidentOpportunityScore:
    duplicate_keys, _duplicate_evidence = _known_duplicate_state(objectives, prior_opportunities)
    duplicate = _dedupe_key(theme) in duplicate_keys
    risk = min(config.score_max, len(risks) * config.risk_penalty)
    cost = min(config.score_max, len(risks) * config.cost_penalty)
    expected_value = min(
        config.score_max,
        config.score_mid
        + len(evidence) * config.evidence_score_step
        + len(outcomes) * config.outcome_score_step,
    )
    novelty = max(
        0,
        config.score_max - (config.duplicate_penalty if duplicate else 0),
    )
    feasibility = max(0, config.score_max - cost)
    operator_alignment = min(
        config.score_max,
        config.score_mid + len(outcomes) * config.outcome_score_step,
    )
    total = expected_value + novelty + feasibility + operator_alignment - risk - cost
    return ResidentOpportunityScore(
        expected_value=expected_value,
        risk=risk,
        cost=cost,
        novelty=novelty,
        feasibility=feasibility,
        operator_alignment=operator_alignment,
        total=total,
    )


def _themes_from_text(text: str, config: ResidentOpportunityConfig) -> list[str]:
    words = [
        word
        for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text.casefold())
        if word not in set(config.stop_words)
    ]
    themes: list[str] = []
    for index, word in enumerate(words):
        phrase = " ".join(words[index : index + 2])
        if phrase and phrase not in themes:
            themes.append(phrase)
    return themes or [_compact_line(text, limit=80)]


def _known_duplicate_state(
    objectives: tuple[ResidentObjective, ...],
    prior_opportunities: tuple[ResidentOpportunityCandidate, ...],
) -> tuple[set[str], set[str]]:
    keys = {
        item.duplicate_key
        for item in prior_opportunities
        if item.stage
        in {
            ResidentOpportunityStage.EXPERIMENT.value,
            ResidentOpportunityStage.COMMITTED_WORK.value,
            ResidentOpportunityStage.REJECTED.value,
            ResidentOpportunityStage.SUPPRESSED.value,
        }
    }
    evidence_keys = set[str]()
    for objective in objectives:
        if objective.status not in {
            ResidentObjectiveStatus.CANCELLED.value,
            ResidentObjectiveStatus.SUPERSEDED.value,
        }:
            keys.add(_dedupe_key(objective.title))
            for source_evidence in objective.source_evidence:
                keys.add(_dedupe_key(source_evidence))
    for opportunity in prior_opportunities:
        evidence_keys.update(_evidence_keys(opportunity.evidence))
    for objective in objectives:
        evidence_keys.update(_evidence_keys(objective.source_evidence))
    return keys, evidence_keys


def _summary_for_theme(
    theme: str,
    signals: tuple[ResidentOpportunitySignal, ...],
) -> str:
    sample = signals[0].summary if signals else theme
    return _compact_line(f"{theme}: {sample}", limit=220)


def _rationale_for_theme(
    theme: str,
    outcomes: tuple[str, ...],
    domain_model: ResidentDomainModel | None,
    config: ResidentOpportunityConfig,
) -> str:
    if outcomes:
        return (
            f"{theme} appears in evidence linked to cared-about outcomes: "
            f"{'; '.join(outcomes[: config.rationale_outcome_limit])}."
        )
    if domain_model is not None and domain_model.current_understanding:
        return (
            f"{theme} may improve the domain model: "
            f"{_compact_line(domain_model.current_understanding, limit=160)}."
        )
    return f"{theme} appeared repeatedly enough to merit a bounded resident experiment."


def _assumptions_for_theme(
    theme: str,
    signals: tuple[ResidentOpportunitySignal, ...],
) -> tuple[str, ...]:
    return (
        f"{theme} is relevant to the mandate rather than only incidental wording.",
        "A read-only experiment can clarify value before committing implementation work.",
        f"Evidence count for this opportunity is {len(signals)} signal(s).",
    )


def _risks_for_theme(
    theme: str,
    mandate: str,
    signals: tuple[ResidentOpportunitySignal, ...],
) -> tuple[str, ...]:
    text = " ".join((theme, mandate, *(signal.summary for signal in signals))).casefold()
    risks: list[str] = []
    if _has_any(text, ("spend", "purchase", "paid", "money", "supplier")):
        risks.append("spending")
    if _has_any(text, ("printer", "print", "machine", "physical", "device")):
        risks.append("physical_operation")
    if _has_any(text, ("customer", "publish", "email", "external", "public")):
        risks.append("external_side_effect")
    return tuple(risks)


def _operator_question_for_risks(theme: str, risks: tuple[str, ...]) -> str:
    if not risks:
        return ""
    return (
        f"May I run a safe experiment around {theme} that could touch "
        f"{', '.join(risks)}? I will avoid side effects until approved."
    )


def _merge_signal_items(
    signals: tuple[ResidentOpportunitySignal, ...],
    field: str,
) -> tuple[str, ...]:
    items: list[str] = []
    for signal in signals:
        values = signal.outcomes if field == "outcomes" else ()
        for value in values:
            if value and value not in items:
                items.append(value)
    return tuple(items)


def _final_next_action(
    selected: tuple[ResidentOpportunityCandidate, ...],
    suppressed: tuple[ResidentOpportunityCandidate, ...],
) -> str:
    if selected:
        return f"Advance safe experiment for: {selected[0].title}"
    if suppressed:
        return "Sleep after recording duplicate or low-value opportunity suppression."
    return "No opportunity evidence found; sleep until memory or research changes."


def _opportunity_line(opportunity: ResidentOpportunityCandidate) -> str:
    return (
        f"{opportunity.id} [{opportunity.stage}] score={opportunity.score.total}: "
        f"{opportunity.title}"
    )


def _signal_line(signal: ResidentOpportunitySignal) -> str:
    return (
        f"{signal.source}/{signal.kind}: {signal.summary} "
        f"({signal.evidence_ref})"
    )


def _metadata(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- ") or ":" not in stripped:
            continue
        key, value = stripped[2:].split(":", 1)
        values[key.strip()] = value.strip()
    return values


def _title(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _section(content: str, title: str) -> str:
    marker = f"## {title}"
    start = content.find(marker)
    if start < 0:
        return ""
    body = content[start + len(marker) :]
    next_header = body.find("\n## ")
    if next_header >= 0:
        body = body[:next_header]
    return body.strip()


def _section_items(content: str, title: str) -> list[str]:
    items: list[str] = []
    for line in _section(content, title).splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if value and value != "(none)":
                items.append(value)
    return items


def _int_value(value: str | None) -> int:
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0


def _dedupe_key(text: str) -> str:
    return _slug(_compact_line(text, limit=100))


def _evidence_keys(evidence: tuple[str, ...]) -> set[str]:
    keys: set[str] = set()
    for item in evidence:
        matched = False
        for match in re.findall(r"https?://[^\)\s]+", item):
            keys.add(match.rstrip(".,;"))
            matched = True
        if not matched:
            keys.add(_dedupe_key(item))
    return keys


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
