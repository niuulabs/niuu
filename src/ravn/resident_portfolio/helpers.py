"""Free helper functions for resident portfolio management."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravn.domain.models import TokenUsage
from ravn.domain.operator_contact import (
    OperatorContactKind,
    OperatorContactPurpose,
    OperatorContactRequest,
    OperatorContactResult,
    OperatorContactStatus,
)
from ravn.domain.resident_continuation import ResidentBudgetSnapshot
from ravn.domain.resident_expert import (
    ExpertArtifact,
    ResidentDomainModel,
    ResidentWorkstreamStatus,
    WorkstreamExecutionResult,
)
from ravn.domain.resident_portfolio import (
    ResidentAutonomyCycleReport,
    ResidentCapabilityDiscoveryResult,
    ResidentCapabilityGap,
    ResidentCapabilityOption,
    ResidentDelegationRecord,
    ResidentDelegationReport,
    ResidentDelegationReview,
    ResidentDelegationReviewDecision,
    ResidentDelegationStatus,
    ResidentExecutionResult,
    ResidentObjective,
    ResidentObjectiveDryRunPreview,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentPortfolioDecisionKind,
    ResidentPortfolioRepairRecord,
    ResidentPortfolioStewardActionKind,
    ResidentPortfolioStewardRun,
    ResidentPortfolioValidationFinding,
    ResidentPortfolioValidationReport,
    ResidentPortfolioValidationSeverity,
    ResidentWorkerBrief,
    ResidentWorkItemBackend,
)
from ravn.domain.wakeful_resident import (
    WakefulResidentRun,
)
from ravn.ports.capability import (
    WorkflowRunEvent,
    WorkflowRunReference,
    WorkflowRunStatus,
)
from ravn.resident_continuation import _compact_line, _slug
from ravn.resident_text import (
    metadata as _metadata,
)
from ravn.resident_text import (
    render_list as _render_list,
)
from ravn.resident_text import (
    section as _section,
)
from ravn.resident_text import (
    section_items as _section_items,
)

from .config import ResidentPortfolioEvidence
from .constants import (
    _ARTIFACT_PREFIX,
    _CONSOLIDATION_PREFIX,
    _WAKE_CYCLE_PREFIX,
    _WORKFLOW_REFERENCE_PREFIX,
    _WORKSTREAM_PREFIX,
)


def _artifact_assimilation_sort_key(artifact: Any) -> tuple[int, str]:
    path = str(getattr(artifact, "path", "") or "")
    name = Path(path).name.casefold()
    priority = 50
    if name in {"final.md", "summary.md", "critique.md"}:
        priority = 0
    elif name in {"manifest.md", "sources.md", "followups.md"}:
        priority = 5
    elif getattr(artifact, "canonical", False):
        priority = 10
    elif name.endswith(".md"):
        priority = 20
    return (priority, path)


def _assimilate_workflow_artifact_excerpts(
    excerpts: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    return {
        "findings": _artifact_section_items(excerpts, ("Findings",)),
        "known_facts": _artifact_section_items(excerpts, ("Known Facts",)),
        "hypotheses": _artifact_section_items(excerpts, ("Hypotheses",)),
        "open_questions": _artifact_section_items(
            excerpts,
            ("Open Questions", "Real Open Questions"),
        ),
        "operator_questions": _artifact_section_items(excerpts, ("Operator Questions",)),
        "risk_notes": _artifact_section_items(excerpts, ("Risk Notes", "Risks", "Caveats")),
        "recommended_next_action": _artifact_section_text_items(
            excerpts,
            ("Recommended Next Action", "Next Action"),
        ),
        "follow_up_suggestions": _artifact_section_items(
            excerpts,
            (
                "Follow-Up Suggestions",
                "Follow Up Suggestions",
                "Suggested Follow-Up Work",
                "Suggested Follow Up Work",
            ),
        ),
    }


def _artifact_section_items(
    excerpts: tuple[str, ...],
    section_names: tuple[str, ...],
) -> tuple[str, ...]:
    items: list[str] = []
    seen: set[str] = set()
    for excerpt in excerpts:
        for name in section_names:
            for item in _markdown_section_list_items(excerpt, name):
                key = item.casefold()
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
    return tuple(items)


def _artifact_section_text_items(
    excerpts: tuple[str, ...],
    section_names: tuple[str, ...],
) -> tuple[str, ...]:
    items: list[str] = []
    seen: set[str] = set()
    for excerpt in excerpts:
        for name in section_names:
            text = " ".join(_markdown_section_lines(excerpt, name)).strip()
            text = _compact_line(text, limit=320)
            if not text or text.casefold() == "none":
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            items.append(text)
    return tuple(items)


def _markdown_section_list_items(content: str, name: str) -> tuple[str, ...]:
    items: list[str] = []
    for line in _markdown_section_lines(content, name):
        item = _markdown_list_item_value(line)
        if item:
            items.append(item)
    return tuple(items)


def _markdown_list_item_value(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("- ") or stripped.startswith("* "):
        value = stripped[2:].strip()
    else:
        head, separator, tail = stripped.partition(". ")
        value = tail.strip() if separator and head.isdigit() else ""
    if not value or value.casefold() == "none":
        return ""
    return _compact_line(value, limit=320)


def _markdown_section_lines(content: str, name: str) -> tuple[str, ...]:
    wanted = f"## {name}".casefold()
    lines = content.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if line.strip().casefold() == wanted:
            start = idx + 1
            break
    if start < 0:
        return ()
    collected: list[str] = []
    for line in lines[start:]:
        if line.strip().startswith("## "):
            break
        if line.strip():
            collected.append(line)
    return tuple(collected)


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
    limit = min(max_selected, remaining_active)
    if limit <= 0:
        return ()
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
    completed_sources = {
        item.source_objective_id
        for item in delegations
        if item.result_refs or item.status == ResidentDelegationStatus.COMPLETED.value
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
        if objective.id in completed_sources:
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
        f"## Known Facts\n\n{_render_list(result.known_facts)}\n\n"
        f"## Hypotheses\n\n{_render_list(result.hypotheses)}\n\n"
        f"## Open Questions\n\n{_render_list(result.open_questions)}\n\n"
        f"## Operator Questions\n\n{_render_list(result.operator_questions)}\n\n"
        f"## Risk Notes\n\n{_render_list(result.risk_notes)}\n\n"
        f"## Recommended Next Action\n\n{result.recommended_next_action or 'none'}\n\n"
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
        f"## Follow-Up Suggestions\n\n{_render_list(review.follow_up_suggestions)}\n\n"
        f"## Operator Questions\n\n{_render_list(review.operator_questions)}\n"
    )


def review_delegation_result(
    source: ResidentObjective | None,
    delegation: ResidentDelegationRecord,
    result: ResidentExecutionResult,
) -> ResidentDelegationReview:
    proof = source.proof_criteria if source is not None else delegation.brief.proof_criteria
    missing: list[str] = []
    operator_questions = _operator_questions_for_result(result)
    if result.blocked_reason:
        decision = ResidentDelegationReviewDecision.BLOCKED.value
        reason = f"Delegated result reported a blocker: {result.blocked_reason}"
    elif operator_questions:
        decision = ResidentDelegationReviewDecision.ASK_OPERATOR.value
        reason = (
            "Delegated result surfaced unresolved questions that should be answered "
            "before more autonomous work."
        )
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
        operator_questions=operator_questions,
    )


_OPERATOR_QUESTION_PREFIX = "Can you help resolve this delegated-work question: "


def _operator_questions_for_result(result: ResidentExecutionResult) -> tuple[str, ...]:
    questions: list[str] = []
    for question in (*result.operator_questions, *result.open_questions):
        # _operator_facing_question filters on the RAW question (it rejects internal
        # jargon like "delegated-work"); the operator-facing prefix is added after so
        # the framing itself is not re-filtered.
        operator_question = _operator_facing_question(question)
        if operator_question and operator_question not in questions:
            questions.append(operator_question)
        if len(questions) >= 3:
            break
    return tuple(f"{_OPERATOR_QUESTION_PREFIX}{question}" for question in questions)


def _operator_facing_question(question: str) -> str:
    text = _compact_line(question, limit=240)
    if not text:
        return ""
    lowered = text.casefold()
    internal_terms = (
        "missing inbox signal",
        "inbox signal",
        "delegated-work",
        "delegation",
        "worker brief",
        "source objective",
        "artifact ref",
        "result session",
    )
    if any(term in lowered for term in internal_terms):
        return ""
    # Questions a delegated worker explicitly raised are surfaced to the operator
    # as-is (minus internal jargon); the _needs_human_answer heuristic is for other
    # call sites and would wrongly drop plain factual questions here.
    if text.endswith("?"):
        return text
    return f"{text}?"


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
        if not isinstance(data, dict):
            # A worker that prints a JSON array/number/string must not crash the
            # executor on data.get(...); treat the raw output as the summary.
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


def _objective_for_delegation_question(
    report: ResidentDelegationReport,
    question: str,
) -> ResidentObjective | None:
    gated = _gated_objective_for_question(report, question)
    if gated is not None:
        return gated
    for objective in report.updated_objectives + report.created_follow_up_objectives:
        if objective.pending_question == question:
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
    approval_required = bool(risks)
    reason = (
        "Resident delegated execution would cross a risk boundary and needs "
        "operator judgment before continuing."
        if approval_required
        else "Resident needs operator context before it can choose the next useful action."
    )
    impact = (
        "A positive answer allows this specific objective to be delegated; "
        "no answer keeps the resident waiting."
        if approval_required
        else "Your answer will be recorded as context and used to steer the resident's next wake."
    )
    return OperatorContactRequest(
        id=contact_id,
        question=question,
        reason=reason,
        impact=impact,
        source_objective_id=objective_id,
        risk_boundaries=risks,
        kind=OperatorContactKind.HELP_NEEDED,
        purpose=(
            OperatorContactPurpose.APPROVAL
            if approval_required
            else OperatorContactPurpose.CLARIFICATION
        ),
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
        result.known_facts,
        result.hypotheses,
        result.open_questions,
    )
    return objective.with_updates(
        status=status,
        pending_question=review.operator_questions[0] if review.operator_questions else "",
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
    facts = result.known_facts or (
        result.findings
        if review.decision == ResidentDelegationReviewDecision.COMPLETE.value
        else ()
    )
    gaps = _delegation_capability_gaps(result, review)
    failure_notes = _delegation_failure_notes(record, result, review)
    open_threads = _merge_text(
        result.open_questions,
        result.operator_questions,
        result.hypotheses,
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


def _domain_model_with_capability_learning(
    model: ResidentDomainModel,
    *,
    result: ResidentCapabilityDiscoveryResult,
    discovery_ref: str,
    follow_ups: tuple[ResidentObjective, ...],
) -> tuple[ResidentDomainModel, WorkstreamExecutionResult]:
    gap = result.gap
    summary = _compact_line(
        f"capability discovery for {gap.capability}: {result.recommended_safe_next_experiment}",
        limit=240,
    )
    facts = _capability_discovery_facts(result, discovery_ref)
    open_threads = _merge_text(
        result.unresolved_questions,
        tuple(f"capability follow-up {item.id}: {item.title}" for item in follow_ups),
    )
    gaps = _capability_discovery_gaps(result)
    consolidation_result = WorkstreamExecutionResult(
        workstream_id=f"capability-discovery-{gap.id}",
        status="completed",
        summary=summary,
        artifact_refs=(discovery_ref,),
        facts=facts,
        lessons=open_threads,
        capability_gaps=gaps,
    )
    artifact = ExpertArtifact(
        title=f"Capability discovery: {gap.capability}",
        kind="capability_evaluation",
        path=discovery_ref,
        purpose="Compact resident memory for a discovered capability path.",
        summary=result.why_it_matters,
    )
    updated = model.with_consolidation(
        recent_outcomes=_merge_text(model.recent_outcomes, (summary,), limit=12),
        known_facts=_merge_text(model.known_facts, facts),
        resident_decisions=_merge_text(
            model.resident_decisions,
            (
                f"capability discovery recommended {result.recommended_option_id}: "
                f"{result.recommended_safe_next_experiment}",
            ),
        ),
        open_threads=_merge_text(model.open_threads, open_threads),
        capability_gaps=_merge_text(model.capability_gaps, gaps),
        memory_hygiene_notes=_merge_text(
            model.memory_hygiene_notes,
            tuple(result.duplicate_check_notes),
        ),
        artifacts=_merge_expert_artifacts(model.artifacts, (artifact,)),
    )
    return updated, consolidation_result


def _capability_discovery_facts(
    result: ResidentCapabilityDiscoveryResult,
    discovery_ref: str,
) -> tuple[str, ...]:
    facts = [
        f"capability discovery persisted: {discovery_ref}",
        f"recommended safe experiment for {result.gap.capability}: "
        f"{result.recommended_safe_next_experiment}",
    ]
    facts.extend(f"existing capability available: {item}" for item in result.existing_capabilities)
    facts.extend(
        f"candidate adapter configuration: {item}" for item in result.configuration_evidence
    )
    facts.extend(
        f"candidate option {item.id}: {item.title}" for item in result.candidate_options[:4]
    )
    return tuple(_compact_line(item, limit=240) for item in facts if item)


def _capability_discovery_gaps(
    result: ResidentCapabilityDiscoveryResult,
) -> tuple[str, ...]:
    if result.existing_capabilities:
        return ()
    risky = tuple(item for item in result.candidate_options if item.approval_required)
    if not risky and not result.unresolved_questions:
        return ()
    return tuple(
        _compact_line(item, limit=220)
        for item in (
            *result.unresolved_questions,
            *(
                f"capability {result.gap.capability} option {item.id} requires approval "
                f"for {', '.join(item.risks)}"
                for item in risky
            ),
        )
        if item
    )


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
    if review.decision == ResidentDelegationReviewDecision.ASK_OPERATOR.value:
        for question in result.operator_questions:
            if len(follow_ups) >= limit:
                break
            text = _compact_line(question, limit=220)
            follow_ups.append(
                _objective_from_text(
                    mandate,
                    title=f"Ask operator: {text}",
                    source=f"{result.session_id}: {text}",
                    kind=ResidentObjectiveKind.OPERATOR_QUESTION,
                    reasoning=(
                        "Delegated execution produced useful evidence but surfaced "
                        "an uncertainty that appears answerable by the operator."
                    ),
                    proof="Operator answer is recorded and used to update resident memory/work.",
                    status=ResidentObjectiveStatus.NEEDS_OPERATOR,
                    dependencies=dependencies,
                    pending_question=text,
                )
            )
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
            _delegated_blocker_follow_up(
                mandate,
                result.blocked_reason,
                dependencies=dependencies,
            )
        )
    return tuple(follow_ups)


def _delegated_blocker_follow_up(
    mandate: str,
    blocked_reason: str,
    *,
    dependencies: tuple[str, ...],
) -> ResidentObjective:
    text = _compact_line(blocked_reason, limit=220)
    if _needs_human_answer(text):
        return _objective_from_text(
            mandate,
            title=f"Ask operator: {text}",
            source=text,
            kind=ResidentObjectiveKind.OPERATOR_QUESTION,
            reasoning="Delegated execution reported a blocker that needs operator input.",
            proof="Operator answer is recorded and converted into policy or follow-up work.",
            status=ResidentObjectiveStatus.NEEDS_OPERATOR,
            dependencies=dependencies,
            pending_question=text,
        )
    return _objective_from_text(
        mandate,
        title=f"Resolve delegated blocker: {text}",
        source=text,
        kind=ResidentObjectiveKind.REVIEW,
        reasoning="Delegated execution reported a blocker that needs resident review.",
        proof="Blocker has a decision, workaround, or replacement objective.",
        dependencies=dependencies,
    )


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
    return _has_any(
        lowered,
        (
            "operator",
            "human",
            "approval",
            "approve",
            "may i",
            "should i",
            "do you want",
            "would you like",
            "provide",
            "choose",
            "which ",
            "preference",
            "priority",
            "matters most",
        ),
    )


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


async def _gather_portfolio_links(
    backend: ResidentWorkItemBackend,
) -> dict[str, tuple[str, ...]]:
    """The standard wake/workstream/artifact/consolidation link bundle for a portfolio."""
    wake, workstream, artifact, consolidation = await asyncio.gather(
        backend.list_refs(_WAKE_CYCLE_PREFIX),
        backend.list_refs(_WORKSTREAM_PREFIX),
        backend.list_refs(_ARTIFACT_PREFIX),
        backend.list_refs(_CONSOLIDATION_PREFIX),
    )
    return {
        "wake_record_links": tuple(wake),
        "workstream_links": tuple(workstream),
        "artifact_links": tuple(artifact),
        "consolidation_links": tuple(consolidation),
    }


async def _review_objective_against_wake(
    backend: ResidentWorkItemBackend,
    objective: ResidentObjective,
    wake_run: WakefulResidentRun,
) -> ResidentObjective:
    """Recompute an objective's status and links from a completed wake run (no persistence)."""
    wake, artifacts, workstream, consolidation = await asyncio.gather(
        backend.list_refs(_WAKE_CYCLE_PREFIX),
        backend.list_refs(_ARTIFACT_PREFIX),
        backend.list_refs(_WORKSTREAM_PREFIX),
        backend.list_refs(_CONSOLIDATION_PREFIX),
    )
    wake_links = tuple(wake)
    # Artifacts THIS wake produced — proof completion must depend on these, not on
    # unrelated artifacts already present in the backend (which would let one
    # objective's evidence complete every other objective).
    wake_artifacts = tuple(ref for cycle in wake_run.cycles for ref in cycle.artifact_refs)
    artifact_links = _merge_text(tuple(artifacts), wake_artifacts)
    workstream_links = tuple(workstream)
    consolidation_links = tuple(consolidation)
    proof_progress = _proof_progress_from_wake(wake_run)
    status = (
        ResidentObjectiveStatus.COMPLETED.value
        if _proof_satisfied(proof_progress, wake_artifacts, ())
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


async def _merge_and_persist_objectives(
    backend: ResidentWorkItemBackend,
    mandate: str,
    objectives: tuple[ResidentObjective, ...],
) -> tuple[ResidentPortfolio, tuple[ResidentObjective, ...]]:
    """Load (or create) the portfolio and persist each merged objective.

    Returns the loaded portfolio and the merged objectives so the caller can
    populate link fields and write the portfolio.
    """
    stored = await backend.read_portfolio(mandate)
    portfolio = stored or ResidentPortfolio(mandate=mandate)
    merged = merge_objectives(objectives)
    for objective in merged:
        await backend.write_objective(objective)
    return portfolio, merged


async def _consolidate_learning(
    expert_memory: Any,
    mandate: str,
    build_model: Callable[
        [ResidentDomainModel], tuple[ResidentDomainModel, WorkstreamExecutionResult]
    ],
) -> tuple[str, ...]:
    """Apply a learning update to the domain model and persist the consolidation."""
    if expert_memory is None:
        return ()
    existing = await expert_memory.read_domain_model(mandate)
    model = existing or ResidentDomainModel(mandate=mandate)
    updated_model, consolidation_result = build_model(model)
    model_ref = await expert_memory.write_domain_model(updated_model)
    consolidation_ref = await expert_memory.write_consolidation(updated_model, consolidation_result)
    return (model_ref, consolidation_ref)


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
        created_at=_datetime_value(metadata.get("created_at")),
        updated_at=_datetime_value(metadata.get("updated_at")),
        last_advanced_at=_optional_datetime_value(metadata.get("last_advanced_at")),
        last_reviewed_at=_optional_datetime_value(metadata.get("last_reviewed_at")),
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


def _title(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return ""


def _section_tail(content: str, name: str) -> str:
    wanted = f"## {name}".casefold()
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if line.strip().casefold() == wanted:
            return "\n".join(lines[idx + 1 :]).strip()
    return ""


def _merge_text(
    *groups: tuple[str, ...],
    limit: int = 40,
    keep_last: bool = False,
) -> tuple[str, ...]:
    merged: list[str] = []
    for group in groups:
        for item in group:
            value = str(item).strip()
            if value and value not in merged:
                merged.append(value)
    # keep_last keeps the NEWEST `limit` items (for append-style logs such as
    # decision_history); the default keeps the first `limit`.
    if keep_last:
        return tuple(merged[-limit:])
    return tuple(merged[:limit])


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


def _optional_datetime_value(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
