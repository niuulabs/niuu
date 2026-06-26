"""Resident opportunity generation runtime."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ravn.adapters.resident_work.mimir import MimirResidentWorkAdapter
from ravn.domain.resident_expert import (
    ResidentDomainExpertMemoryPort,
    ResidentDomainModel,
    WorkstreamExecutionResult,
)
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
    _merge_text,
    _render_list,
    merge_objectives,
)
from ravn.resident_text import timestamp_slug

_OPPORTUNITY_PREFIX = "resident/opportunities"
_OPPORTUNITY_REPORT_PREFIX = "resident/opportunity-reports"
_ENVIRONMENT_SIGNAL_PREFIX = "resident/environment-signals"
_ENVIRONMENT_SIGNAL_JSON_START = "<resident-environment-signal-json>"
_ENVIRONMENT_SIGNAL_JSON_END = "</resident-environment-signal-json>"


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
        "experiment",
        "explore",
        "from",
        "have",
        "into",
        "more",
        "resident",
        "opportunity",
        "sells",
        "safe",
        "selected",
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
        persisted_refs.extend(
            await self._consolidate_opportunity_learning(
                mandate,
                domain_model=domain_model,
                selected=selected,
                suppressed=suppressed,
                duplicate_notes=duplicate_notes,
            )
        )

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

    async def _consolidate_opportunity_learning(
        self,
        mandate: str,
        *,
        domain_model: ResidentDomainModel | None,
        selected: tuple[ResidentOpportunityCandidate, ...],
        suppressed: tuple[ResidentOpportunityCandidate, ...],
        duplicate_notes: tuple[str, ...],
    ) -> tuple[str, ...]:
        if self._expert_memory is None:
            return ()
        if not selected and not suppressed and not duplicate_notes:
            return ()
        model = domain_model or ResidentDomainModel(mandate=mandate)
        result = _opportunity_consolidation_result(selected, suppressed, duplicate_notes)
        updated = _domain_model_with_opportunity_learning(
            model,
            selected=selected,
            suppressed=suppressed,
            duplicate_notes=duplicate_notes,
            result=result,
        )
        model_ref = await self._expert_memory.write_domain_model(updated)
        consolidation_ref = await self._expert_memory.write_consolidation(updated, result)
        return (model_ref, consolidation_ref)

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
        stamp = timestamp_slug(datetime.now(UTC))
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
        stamp = timestamp_slug(datetime.now(UTC))
        path = f"{_OPPORTUNITY_REPORT_PREFIX}/{stamp}.md"
        await self._mimir.upsert_page(path, render_opportunity_report(report))
        return path


class MimirEnvironmentSignalOpportunitySource(ResidentOpportunitySourcePort):
    """Expose observed environment signals as resident opportunity evidence."""

    def __init__(
        self,
        mimir: MimirPort,
        *,
        prefix: str = _ENVIRONMENT_SIGNAL_PREFIX,
    ) -> None:
        self._mimir = mimir
        self._prefix = prefix.strip("/").strip() or _ENVIRONMENT_SIGNAL_PREFIX

    async def write_event(self, event: Any) -> str:
        """Persist a Sleipnir signal event for later resident attention."""
        record = _environment_signal_record(event)
        path = (
            f"{self._prefix}/"
            f"{_environment_timestamp_slug(str(record['observed_at']))}-"
            f"{_slug(record['id']) or 'signal'}.md"
        )
        await self._mimir.upsert_page(path, render_environment_signal_record(record))
        return path

    async def collect(
        self,
        *,
        mandate: str,
        domain_model: ResidentDomainModel | None,
        objectives: tuple[ResidentObjective, ...],
        limit: int,
    ) -> tuple[ResidentOpportunitySignal, ...]:
        del mandate, domain_model
        if limit <= 0:
            return ()
        used_refs = {
            source_evidence
            for objective in objectives
            for source_evidence in objective.source_evidence
        }
        metas = await self._mimir.list_pages(prefix=self._prefix)
        signals: list[ResidentOpportunitySignal] = []
        for meta in sorted(metas, key=lambda item: getattr(item, "path", ""), reverse=True):
            path = str(getattr(meta, "path", "") or "")
            if not path:
                continue
            if any(path in source_evidence for source_evidence in used_refs):
                continue
            try:
                record = parse_environment_signal_record(await self._mimir.read_page(path))
            except FileNotFoundError:
                continue
            if record is None:
                continue
            signals.append(
                ResidentOpportunitySignal(
                    id=str(record.get("id") or path),
                    source=str(record.get("source") or "environment_signal"),
                    kind=str(record.get("kind") or "environment_signal"),
                    summary=str(record.get("summary") or "Environment signal observed."),
                    evidence_ref=path,
                    themes=_record_strings(record.get("themes")),
                    outcomes=_record_strings(record.get("outcomes")),
                    confidence=float(record.get("confidence") or 0.5),
                )
            )
            if len(signals) >= limit:
                break
        return tuple(signals)


def build_mimir_opportunity_runtime(
    *,
    mimir: MimirPort,
    sources: tuple[ResidentOpportunitySourcePort, ...],
    expert_memory: ResidentDomainExpertMemoryPort | None = None,
    config: ResidentOpportunityConfig | None = None,
) -> ResidentOpportunityRuntime:
    """Build a resident opportunity runtime backed by the existing Mimir store."""

    return ResidentOpportunityRuntime(
        backend=MimirResidentWorkAdapter(mimir),
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


def render_environment_signal_record(record: dict[str, Any]) -> str:
    summary = _compact_line(str(record.get("summary") or "Environment signal"), limit=120)
    payload = json.dumps(record, indent=2, sort_keys=True, default=str)
    return (
        f"# Environment Signal: {summary}\n\n"
        f"- id: {record.get('id', '')}\n"
        f"- kind: {record.get('kind', '')}\n"
        f"- source: {record.get('source', '')}\n"
        f"- severity: {record.get('severity', '')}\n"
        f"- observed_at: {record.get('observed_at', '')}\n\n"
        f"{_ENVIRONMENT_SIGNAL_JSON_START}\n"
        f"{payload}\n"
        f"{_ENVIRONMENT_SIGNAL_JSON_END}\n"
    )


def parse_environment_signal_record(content: str) -> dict[str, Any] | None:
    start = content.find(_ENVIRONMENT_SIGNAL_JSON_START)
    end = content.find(_ENVIRONMENT_SIGNAL_JSON_END)
    if start < 0 or end <= start:
        return None
    raw = content[start + len(_ENVIRONMENT_SIGNAL_JSON_START) : end].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _opportunity_consolidation_result(
    selected: tuple[ResidentOpportunityCandidate, ...],
    suppressed: tuple[ResidentOpportunityCandidate, ...],
    duplicate_notes: tuple[str, ...],
) -> WorkstreamExecutionResult:
    selected_titles = tuple(item.title for item in selected)
    suppressed_titles = tuple(item.title for item in suppressed[:4])
    summary = (
        f"Selected {len(selected)} opportunity(ies), suppressed {len(suppressed)} "
        "opportunity candidate(s)."
    )
    return WorkstreamExecutionResult(
        workstream_id="resident-opportunity-generation",
        status="completed" if selected else "sleep",
        summary=summary,
        artifact_refs=tuple(f"{_OPPORTUNITY_PREFIX}/{item.id}.md" for item in selected),
        facts=selected_titles,
        lessons=(
            *tuple(f"selected opportunity: {item.summary}" for item in selected),
            *tuple(f"suppressed opportunity: {item}" for item in suppressed_titles),
            *duplicate_notes,
        ),
    )


def _domain_model_with_opportunity_learning(
    model: ResidentDomainModel,
    *,
    selected: tuple[ResidentOpportunityCandidate, ...],
    suppressed: tuple[ResidentOpportunityCandidate, ...],
    duplicate_notes: tuple[str, ...],
    result: WorkstreamExecutionResult,
) -> ResidentDomainModel:
    selected_items = tuple(
        f"{item.title} | safe next experiment: {item.safe_next_experiment}"
        for item in selected
    )
    open_threads = tuple(
        f"opportunity experiment pending: {item.duplicate_key}" for item in selected
    )
    hygiene_notes = tuple(
        f"opportunity suppressed: {item.duplicate_key}" for item in suppressed[:4]
    ) + duplicate_notes
    return model.with_consolidation(
        recent_outcomes=_merge_text(model.recent_outcomes, (result.summary,)),
        known_facts=_merge_text(model.known_facts, result.facts),
        resident_decisions=_merge_text(
            model.resident_decisions,
            tuple(f"selected opportunity for safe experiment: {item.title}" for item in selected),
        ),
        open_threads=_merge_text(model.open_threads, open_threads),
        memory_hygiene_notes=_merge_text(model.memory_hygiene_notes, hygiene_notes),
        opportunities=_merge_text(model.opportunities, selected_items),
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
        if _known_duplicate_key(candidate.duplicate_key, duplicate_keys):
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
        if "safe next experiment:" in item.casefold():
            continue
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
    duplicate = _known_duplicate_key(_dedupe_key(theme), duplicate_keys)
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
    # feasibility already encodes the cost penalty (score_max - cost); subtracting
    # cost again here double-counted it and made the score non-monotonic.
    total = expected_value + novelty + feasibility + operator_alignment - risk
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


def _known_duplicate_key(candidate_key: str, known_keys: set[str]) -> bool:
    key = candidate_key.strip("-")
    if not key:
        return False
    for known in known_keys:
        known = known.strip("-")
        if not known:
            continue
        if key == known or key.startswith(f"{known}-") or known.startswith(f"{key}-"):
            return True
    return False


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


def _environment_signal_record(event: Any) -> dict[str, Any]:
    payload = dict(getattr(event, "payload", {}) or {})
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    event_type = str(getattr(event, "event_type", "") or payload.get("event_type") or "")
    severity = str(payload.get("severity") or data.get("severity") or "info")
    event_id = str(
        getattr(event, "event_id", "")
        or payload.get("event_id")
        or data.get("provider_event_id")
        or data.get("dedupe_key")
        or getattr(event, "correlation_id", "")
        or event_type
    )
    observed_at = _event_timestamp(event, data)
    summary = _environment_signal_summary(event, payload, data, severity)
    themes = _environment_signal_themes(payload, data, summary)
    return {
        "id": event_id,
        "source": str(data.get("source_id") or payload.get("signal_source") or "environment"),
        "kind": event_type or str(payload.get("signal_kind") or "environment_signal"),
        "summary": summary,
        "evidence_ref": str(data.get("raw_payload_ref") or ""),
        "severity": severity,
        "observed_at": observed_at,
        "themes": themes,
        "outcomes": _environment_signal_outcomes(severity),
        "confidence": _environment_signal_confidence(severity),
        "payload": payload,
    }


def _event_timestamp(event: Any, data: dict[str, Any]) -> str:
    raw = data.get("observed_at") or getattr(event, "timestamp", None)
    if hasattr(raw, "isoformat"):
        return raw.isoformat()
    text = str(raw or "").strip()
    if text:
        return text
    return datetime.now(UTC).isoformat()


def _environment_signal_summary(
    event: Any,
    payload: dict[str, Any],
    data: dict[str, Any],
    severity: str,
) -> str:
    message = str(
        data.get("message")
        or data.get("reason")
        or data.get("title")
        or payload.get("summary")
        or ""
    ).strip()
    explicit = str(getattr(event, "summary", "") or "").strip()
    if explicit:
        if message and message not in explicit:
            return _compact_line(f"{explicit}: {message}", limit=220)
        return explicit
    object_ref = data.get("object_ref")
    if not isinstance(object_ref, dict):
        object_ref = {}
    object_label = " ".join(
        str(object_ref.get(key) or "").strip()
        for key in ("kind", "namespace", "name")
        if str(object_ref.get(key) or "").strip()
    )
    signal_kind = str(payload.get("signal_kind") or payload.get("kind") or "").strip()
    parts = [part for part in (signal_kind, object_label, severity, message) if part]
    return _compact_line(" - ".join(parts) or "Environment signal observed.", limit=220)


def _environment_signal_themes(
    payload: dict[str, Any],
    data: dict[str, Any],
    summary: str,
) -> tuple[str, ...]:
    themes: list[str] = []
    for value in (
        payload.get("signal_kind"),
        data.get("source_id"),
        data.get("provider"),
    ):
        text = _compact_line(str(value or ""), limit=80)
        if text and text not in themes:
            themes.append(text)
    object_ref = data.get("object_ref")
    if isinstance(object_ref, dict):
        object_kind = str(object_ref.get("kind") or "").strip()
        object_name = str(object_ref.get("name") or "").strip()
        if object_kind and object_name:
            themes.append(_compact_line(f"{object_kind} {object_name}", limit=80))
        elif object_kind:
            themes.append(_compact_line(object_kind, limit=80))
    if not themes:
        themes.extend(_themes_from_text(summary, ResidentOpportunityConfig())[:3])
    return tuple(dict.fromkeys(item for item in themes if item))


def _environment_signal_outcomes(severity: str) -> tuple[str, ...]:
    outcomes = ["environment health", "reliable operation"]
    if severity in {"warning", "critical"}:
        outcomes.append("faster response")
    return tuple(outcomes)


def _environment_signal_confidence(severity: str) -> float:
    if severity == "critical":
        return 0.9
    if severity == "warning":
        return 0.75
    return 0.6


def _environment_timestamp_slug(value: str) -> str:
    compact = re.sub(r"[^0-9A-Za-z]+", "", value)
    return compact[:20] or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _record_strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, list | tuple):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


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
