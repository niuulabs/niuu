"""Resident domain-expert loop V0.

This layer sits above bounded continuation.  It keeps a compact domain model,
creates self-authored workstreams, advances a bounded number of them through a
backend-neutral execution port, and consolidates the result back into memory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import (
    ResidentActionCandidate,
    ResidentBudgetLimits,
    ResidentContinuationContext,
    ResidentMemoryEntry,
    ResidentPolicyObservation,
    ResidentTurnRecord,
)
from ravn.domain.resident_expert import (
    ExpertArtifact,
    ExpertLoopDecision,
    ExpertLoopDecisionKind,
    ResidentDomainExpertMemoryPort,
    ResidentDomainExpertRun,
    ResidentDomainModel,
    ResidentWorkstream,
    ResidentWorkstreamKind,
    ResidentWorkstreamStatus,
    WorkProductKind,
    WorkstreamExecutionPort,
    WorkstreamExecutionResult,
)
from ravn.ports.executor import ExecutionAgentPort
from ravn.ports.mimir import MimirPort
from ravn.resident_continuation import (
    ConfigurableResidentPolicy,
    NullResidentMemory,
    ResidentContinuationKernel,
    ResidentPolicyPort,
    ResidentRunBudget,
    _compact_line,
    _parse_outcome_fields,
    _slug,
)

_DOMAIN_MODEL_PATH = "resident/domain-expert/domain-model.md"
_PATH_RE = re.compile(r"(?P<path>[A-Za-z0-9_./-]+\.(?:md|markdown|yaml|yml|json|csv|txt))")


@dataclass(frozen=True)
class ResidentDomainExpertConfig:
    """Bounds for one domain-expert loop invocation."""

    orientation_turns: int = 1
    max_active_workstreams: int = 1
    max_workstream_turns: int = 1
    max_wall_clock_seconds: float = 900.0
    max_tokens: int = 0


class LocalResidentDomainExpertMemory(ResidentDomainExpertMemoryPort):
    """Filesystem-backed domain-expert memory."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    async def read_domain_model(self, mandate: str) -> ResidentDomainModel | None:
        path = self._root / _DOMAIN_MODEL_PATH
        if not path.exists():
            return None
        return _parse_domain_model_page(path.read_text(encoding="utf-8"), mandate=mandate)

    async def write_domain_model(self, model: ResidentDomainModel) -> str:
        return self._write(Path(_DOMAIN_MODEL_PATH), _render_domain_model(model))

    async def list_workstreams(self, domain_model_ref: str) -> list[ResidentWorkstream]:
        base = self._root / "resident" / "domain-expert" / "workstreams"
        if not base.exists():
            return []
        workstreams: list[ResidentWorkstream] = []
        for path in sorted(base.glob("*.md")):
            parsed = _parse_workstream_page(path.read_text(encoding="utf-8"))
            if parsed is not None:
                workstreams.append(parsed)
        return workstreams

    async def write_workstream(self, workstream: ResidentWorkstream) -> str:
        rel = Path("resident/domain-expert/workstreams") / f"{workstream.id}.md"
        return self._write(rel, _render_workstream(workstream))

    async def write_artifact(self, artifact: ExpertArtifact, content: str) -> str:
        rel = Path("resident/domain-expert/artifacts") / f"{_slug(artifact.title)}.md"
        return self._write(rel, content)

    async def write_consolidation(
        self,
        model: ResidentDomainModel,
        result: WorkstreamExecutionResult,
    ) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        rel = Path("resident/domain-expert/consolidations") / f"{stamp}-{result.workstream_id}.md"
        return self._write(rel, _render_consolidation(model, result))

    def _write(self, rel: Path, content: str) -> str:
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(rel)


class MimirResidentDomainExpertMemory(ResidentDomainExpertMemoryPort):
    """Mimir-backed domain-expert memory."""

    def __init__(self, mimir: MimirPort) -> None:
        self._mimir = mimir

    async def read_domain_model(self, mandate: str) -> ResidentDomainModel | None:
        try:
            content = await self._mimir.read_page(_DOMAIN_MODEL_PATH)
        except FileNotFoundError:
            return None
        return _parse_domain_model_page(content, mandate=mandate)

    async def write_domain_model(self, model: ResidentDomainModel) -> str:
        await self._mimir.upsert_page(_DOMAIN_MODEL_PATH, _render_domain_model(model))
        return _DOMAIN_MODEL_PATH

    async def list_workstreams(self, domain_model_ref: str) -> list[ResidentWorkstream]:
        pages = await self._mimir.list_pages(prefix="resident/domain-expert/workstreams")
        workstreams: list[ResidentWorkstream] = []
        for meta in pages:
            try:
                content = await self._mimir.read_page(meta.path)
            except FileNotFoundError:
                continue
            parsed = _parse_workstream_page(content)
            if parsed is not None:
                workstreams.append(parsed)
        return workstreams

    async def write_workstream(self, workstream: ResidentWorkstream) -> str:
        path = f"resident/domain-expert/workstreams/{workstream.id}.md"
        await self._mimir.upsert_page(path, _render_workstream(workstream))
        return path

    async def write_artifact(self, artifact: ExpertArtifact, content: str) -> str:
        path = f"resident/domain-expert/artifacts/{_slug(artifact.title)}.md"
        await self._mimir.upsert_page(path, content)
        return path

    async def write_consolidation(
        self,
        model: ResidentDomainModel,
        result: WorkstreamExecutionResult,
    ) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = f"resident/domain-expert/consolidations/{stamp}-{result.workstream_id}.md"
        await self._mimir.upsert_page(path, _render_consolidation(model, result))
        return path


class LocalAgentWorkstreamExecutor(WorkstreamExecutionPort):
    """Advance a workstream through an existing execution agent."""

    def __init__(
        self,
        agent: ExecutionAgentPort,
        *,
        persona_config: Any | None = None,
    ) -> None:
        self._agent = agent
        self._persona_config = persona_config

    async def advance(
        self,
        workstream: ResidentWorkstream,
        *,
        domain_model: ResidentDomainModel,
    ) -> WorkstreamExecutionResult:
        result = await self._agent.run_turn(_build_workstream_prompt(workstream, domain_model))
        outcome = _parse_outcome_fields(result.response, self._persona_config)
        refs = _extract_artifact_refs(result.response, outcome)
        summary = str(outcome.get("orientation_summary") or "").strip() or _compact_line(
            result.response,
            limit=500,
        )
        facts = tuple(_string_items(outcome.get("domain_hypotheses")))[:5]
        gaps = tuple(_string_items(outcome.get("capability_gaps")))[:5]
        return WorkstreamExecutionResult(
            workstream_id=workstream.id,
            status=ResidentWorkstreamStatus.COMPLETED.value,
            summary=summary,
            artifact_refs=refs,
            facts=facts,
            lessons=tuple(_string_items(outcome.get("self_authored_work")))[:5],
            capability_gaps=gaps,
            usage=result.usage,
        )


class UnavailableWorkstreamExecutor(WorkstreamExecutionPort):
    """Records a capability gap when a desired backend is unavailable."""

    def __init__(self, backend_name: str = "remote execution") -> None:
        self._backend_name = backend_name

    async def advance(
        self,
        workstream: ResidentWorkstream,
        *,
        domain_model: ResidentDomainModel,
    ) -> WorkstreamExecutionResult:
        return WorkstreamExecutionResult(
            workstream_id=workstream.id,
            status=ResidentWorkstreamStatus.PAUSED.value,
            summary=f"{self._backend_name} unavailable; paused workstream instead of failing hard.",
            capability_gaps=(f"{self._backend_name} unavailable for workstream execution",),
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )


class ResidentDomainExpertLoop:
    """First bounded resident domain-expert loop."""

    def __init__(
        self,
        *,
        agent: ExecutionAgentPort,
        expert_memory: ResidentDomainExpertMemoryPort,
        continuation_memory: Any | None = None,
        policy: ResidentPolicyPort | None = None,
        executor: WorkstreamExecutionPort | None = None,
        persona_config: Any | None = None,
        config: ResidentDomainExpertConfig | None = None,
        ask_operator: Any | None = None,
    ) -> None:
        self._agent = agent
        self._expert_memory = expert_memory
        self._continuation_memory = continuation_memory or NullResidentMemory()
        self._policy = policy or ConfigurableResidentPolicy()
        self._executor = executor or LocalAgentWorkstreamExecutor(
            agent,
            persona_config=persona_config,
        )
        self._persona_config = persona_config
        self._config = config or ResidentDomainExpertConfig()
        self._ask_operator = ask_operator

    async def run(self, mandate: str) -> ResidentDomainExpertRun:
        prior_model = await self._expert_memory.read_domain_model(mandate)
        orientation_budget = ResidentRunBudget(
            ResidentBudgetLimits(
                max_turns=self._config.orientation_turns,
                max_wall_clock_seconds=self._config.max_wall_clock_seconds,
                max_tokens=self._config.max_tokens,
            )
        )
        continuation = ResidentContinuationKernel(
            agent=self._agent,
            memory=self._continuation_memory,
            policy=self._policy,
            budget=orientation_budget,
            persona_config=self._persona_config,
            ask_operator=self._ask_operator,
        )
        continuation_run = await continuation.run(mandate)
        model = build_domain_model_from_continuation(
            mandate,
            prior_model=prior_model,
            turns=continuation_run.turns,
        )
        model_ref = await self._expert_memory.write_domain_model(model)

        existing = await self._expert_memory.list_workstreams(model_ref)
        existing_to_advance = _select_workstreams_to_advance(
            existing,
            limit=self._config.max_active_workstreams,
        )
        planned = plan_workstreams(
            model.with_workstreams(tuple(existing)),
            domain_model_ref=model_ref,
            existing=tuple(existing),
            limit=max(0, self._config.max_active_workstreams - len(existing_to_advance)),
        )
        all_workstreams = tuple(existing) + planned
        for workstream in planned:
            await self._expert_memory.write_workstream(workstream)
        model = model.with_workstreams(all_workstreams)
        model_ref = await self._expert_memory.write_domain_model(model)

        workstream_budget = ResidentRunBudget(
            ResidentBudgetLimits(
                max_turns=self._config.max_workstream_turns,
                max_wall_clock_seconds=self._config.max_wall_clock_seconds,
                max_tokens=self._config.max_tokens,
            )
        )
        decisions: list[ExpertLoopDecision] = []
        execution_results: list[WorkstreamExecutionResult] = []
        artifacts: list[ExpertArtifact] = list(model.artifacts)
        selected_workstreams = existing_to_advance + planned

        for workstream in selected_workstreams[: self._config.max_active_workstreams]:
            budget_decision = workstream_budget.can_continue()
            if not budget_decision.allowed:
                decisions.append(
                    ExpertLoopDecision(
                        kind=ExpertLoopDecisionKind.STOP,
                        reason=budget_decision.reason,
                        workstream=workstream,
                    )
                )
                break

            policy_decision = await self._policy.assess(
                _action_from_workstream(workstream),
                context=_policy_context(mandate, continuation_run.turns, workstream_budget),
            )
            if policy_decision.needs_approval:
                paused = workstream.with_status(ResidentWorkstreamStatus.NEEDS_OPERATOR.value)
                await self._expert_memory.write_workstream(paused)
                model = model.with_workstreams(
                    tuple(
                        paused if item.id == paused.id else item
                        for item in model.active_workstreams
                    )
                )
                model_ref = await self._expert_memory.write_domain_model(model)
                decisions.append(
                    ExpertLoopDecision(
                        kind=ExpertLoopDecisionKind.ASK_OPERATOR,
                        reason=policy_decision.reason,
                        workstream=paused,
                        question=policy_decision.question,
                    )
                )
                if self._ask_operator is not None and policy_decision.question:
                    await self._ask_operator(policy_decision.question)
                continue

            active = workstream.activate()
            await self._expert_memory.write_workstream(active)
            result = await self._executor.advance(active, domain_model=model)
            if isinstance(result.usage, TokenUsage):
                workstream_budget.record_usage(result.usage)
            execution_results.append(result)

            updated = active.with_status(result.status)
            await self._expert_memory.write_workstream(updated)
            artifact = _artifact_from_result(updated, result)
            artifact_ref = await self._expert_memory.write_artifact(
                artifact,
                _render_execution_artifact(model, updated, result),
            )
            artifact = ExpertArtifact(
                title=artifact.title,
                kind=artifact.kind,
                path=artifact_ref,
                purpose=artifact.purpose,
                summary=artifact.summary,
                created_at=artifact.created_at,
            )
            artifacts.append(artifact)
            model = consolidate_workstream_result(
                model,
                updated,
                result,
                artifact=artifact,
            )
            await self._expert_memory.write_consolidation(model, result)
            model_ref = await self._expert_memory.write_domain_model(model)

            next_budget = workstream_budget.can_continue()
            decisions.append(
                ExpertLoopDecision(
                    kind=ExpertLoopDecisionKind.STOP
                    if not next_budget.allowed
                    else ExpertLoopDecisionKind.SLEEP,
                    reason=next_budget.reason
                    if not next_budget.allowed
                    else "bounded expert workstream advanced; sleeping until new signal or budget",
                    workstream=updated,
                )
            )
            if not next_budget.allowed:
                break

        if not decisions:
            decisions.append(
                ExpertLoopDecision(
                    kind=ExpertLoopDecisionKind.SLEEP,
                    reason="no new workstream selected",
                )
            )

        return ResidentDomainExpertRun(
            mandate=mandate,
            domain_model_ref=model_ref,
            domain_model=model,
            workstreams=model.active_workstreams,
            artifacts=tuple(artifacts),
            execution_results=tuple(execution_results),
            decisions=tuple(decisions),
            budget=workstream_budget.snapshot(),
        )


def build_domain_model_from_continuation(
    mandate: str,
    *,
    prior_model: ResidentDomainModel | None,
    turns: tuple[Any, ...],
) -> ResidentDomainModel:
    last = turns[-1] if turns else None
    fields = getattr(last, "outcome_fields", {}) if last is not None else {}
    tool_names = tuple(getattr(last, "tool_names", ()) if last is not None else ())
    current = str(fields.get("orientation_summary") or "").strip()
    if not current and prior_model is not None:
        current = prior_model.current_understanding

    hypotheses = _merge_items(
        prior_model.hypotheses if prior_model is not None else (),
        _outcome_items(fields, last, "domain_hypotheses"),
    )
    open_questions = _merge_items(
        prior_model.open_questions if prior_model is not None else (),
        _outcome_items(fields, last, "open_questions"),
    )
    gaps = _merge_items(
        prior_model.capability_gaps if prior_model is not None else (),
        _outcome_items(fields, last, "capability_gaps"),
    )
    opportunities = _merge_items(
        prior_model.opportunities if prior_model is not None else (),
        _selected_action_items(fields),
        _outcome_items(fields, last, "self_authored_work"),
    )
    known_facts = _merge_items(
        prior_model.known_facts if prior_model is not None else (),
        (f"resident turn used tools: {', '.join(tool_names)}",) if tool_names else (),
    )
    recent_outcomes = _merge_items(
        prior_model.recent_outcomes if prior_model is not None else (),
        (str(fields.get("rationale") or "").strip(),),
        max_items=12,
    )
    risks = _merge_items(
        prior_model.domain_risks if prior_model is not None else (),
        _risk_like_items(gaps + _outcome_items(fields, last, "open_questions")),
    )

    return ResidentDomainModel(
        mandate=mandate,
        current_understanding=current,
        hypotheses=hypotheses,
        known_facts=known_facts,
        open_questions=open_questions,
        domain_risks=risks,
        opportunities=opportunities,
        capability_gaps=gaps,
        active_workstreams=prior_model.active_workstreams if prior_model is not None else (),
        recent_outcomes=recent_outcomes,
        learned_policy_observations=prior_model.learned_policy_observations
        if prior_model is not None
        else (),
        artifacts=prior_model.artifacts if prior_model is not None else (),
    )


def plan_workstreams(
    model: ResidentDomainModel,
    *,
    domain_model_ref: str,
    existing: tuple[ResidentWorkstream, ...] = (),
    limit: int = 1,
) -> tuple[ResidentWorkstream, ...]:
    existing_titles = {item.title.casefold() for item in existing}
    candidates = model.opportunities or model.hypotheses or ("Map the domain's next useful work",)
    planned: list[ResidentWorkstream] = []
    for candidate in candidates:
        title = _clean_title(candidate)
        if not title or title.casefold() in existing_titles:
            continue
        kind = choose_workstream_kind(candidate)
        product = choose_work_product_kind(candidate)
        workstream = ResidentWorkstream(
            id=_slug(title) or f"workstream-{len(planned) + 1}",
            title=title,
            purpose=f"Advance resident domain expertise through {product.value.replace('_', ' ')}.",
            serves=candidate,
            expected_artifact=product.value,
            kind=kind.value,
            required_capabilities=_required_capabilities_for_kind(kind),
            risk_boundaries=(),
            budget_estimate="small",
            parent_domain_model_ref=domain_model_ref,
            selected_work_product=product.value,
        )
        planned.append(workstream)
        if len(planned) >= limit:
            break
    return tuple(planned)


def _select_workstreams_to_advance(
    workstreams: list[ResidentWorkstream],
    *,
    limit: int,
) -> tuple[ResidentWorkstream, ...]:
    if limit <= 0:
        return ()
    actionable_statuses = {
        ResidentWorkstreamStatus.PROPOSED.value,
        ResidentWorkstreamStatus.ACTIVE.value,
        ResidentWorkstreamStatus.PAUSED.value,
    }
    selected: list[ResidentWorkstream] = []
    for workstream in workstreams:
        if workstream.status not in actionable_statuses:
            continue
        selected.append(workstream)
        if len(selected) >= limit:
            break
    return tuple(selected)


def choose_work_product_kind(text: str) -> WorkProductKind:
    lowered = text.casefold()
    if _has_any(lowered, ("research", "survey", "compare", "investigate")):
        return WorkProductKind.RESEARCH_BRIEF
    if _has_any(lowered, ("system requirement", "srd")):
        return WorkProductKind.SRD
    if _has_any(lowered, ("requirement", "product requirement", "prd")):
        return WorkProductKind.PRD
    if _has_any(lowered, ("architecture", "interface", "design")):
        return WorkProductKind.TECHNICAL_DESIGN
    if _has_any(lowered, ("verify", "test", "qa", "quality")):
        return WorkProductKind.VERIFICATION_PLAN
    if _has_any(lowered, ("experiment", "hypothesis", "trial")):
        return WorkProductKind.EXPERIMENT_PLAN
    if _has_any(lowered, ("implement", "build", "prototype")):
        return WorkProductKind.IMPLEMENTATION_PLAN
    if _has_any(lowered, ("operate", "procedure", "process", "runbook", "checklist", "ledger")):
        return WorkProductKind.OPERATING_PROCEDURE
    if _has_any(lowered, ("review", "learned", "retrospective")):
        return WorkProductKind.RETROSPECTIVE
    if _has_any(lowered, ("capability", "tool", "workflow")):
        return WorkProductKind.CAPABILITY_EVALUATION
    return WorkProductKind.DOMAIN_BRIEF


def choose_workstream_kind(text: str) -> ResidentWorkstreamKind:
    lowered = text.casefold()
    if _has_any(lowered, ("research", "investigate", "compare")):
        return ResidentWorkstreamKind.RESEARCH
    if _has_any(lowered, ("spec", "requirement", "prd", "srd", "design")):
        return ResidentWorkstreamKind.SPECIFICATION
    if _has_any(lowered, ("implement", "build", "prototype")):
        return ResidentWorkstreamKind.IMPLEMENTATION
    if _has_any(lowered, ("verify", "test", "qa")):
        return ResidentWorkstreamKind.VERIFICATION
    if _has_any(lowered, ("workflow", "tool", "capability")):
        return ResidentWorkstreamKind.WORKFLOW_BUILDING
    return ResidentWorkstreamKind.LOCAL_FOLLOW_UP


def consolidate_workstream_result(
    model: ResidentDomainModel,
    workstream: ResidentWorkstream,
    result: WorkstreamExecutionResult,
    *,
    artifact: ExpertArtifact,
) -> ResidentDomainModel:
    workstreams = tuple(
        workstream if item.id == workstream.id else item for item in model.active_workstreams
    )
    facts = _merge_items(model.known_facts, result.facts)
    gaps = _merge_items(model.capability_gaps, result.capability_gaps)
    outcomes = _merge_items(model.recent_outcomes, (result.summary,), max_items=12)
    artifacts = _merge_artifacts(model.artifacts, (artifact,))
    policy_observations = _merge_policy_observations(
        model.learned_policy_observations,
        result.policy_observations,
    )
    return model.with_consolidation(
        workstreams=workstreams,
        recent_outcomes=outcomes,
        known_facts=facts,
        capability_gaps=gaps,
        learned_policy_observations=policy_observations,
        artifacts=artifacts,
    )


def _build_workstream_prompt(
    workstream: ResidentWorkstream,
    model: ResidentDomainModel,
) -> str:
    return (
        "Advance this resident self-authored workstream as a bounded domain-expert step. "
        "This is not a new operator task; it follows from the resident domain model.\n\n"
        f"Mandate:\n{model.mandate}\n\n"
        f"Current understanding:\n{model.current_understanding or 'unknown'}\n\n"
        f"Workstream title: {workstream.title}\n"
        f"Purpose: {workstream.purpose}\n"
        f"Serves: {workstream.serves}\n"
        f"Expected artifact kind: {workstream.expected_artifact}\n\n"
        "Produce one durable expert artifact appropriate to the workstream. "
        "Use workspace or Mimir tools when available. Keep it compact but useful. "
        "Do not spend money, send external messages, deploy to production, use credentials "
        "in new ways, or operate physical machines unless explicitly approved. "
        "Finish with the persona's structured outcome and include paths to artifacts in context."
    )


def _policy_context(
    mandate: str,
    turns: tuple[Any, ...],
    budget: ResidentRunBudget,
) -> ResidentContinuationContext:
    if turns:
        record = turns[-1]
    else:
        record = ResidentTurnRecord(
            turn_index=0,
            prompt=mandate,
            response="",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )
    return ResidentContinuationContext(
        mandate=mandate,
        turn_record=record,
        budget=budget.snapshot(),
        recent_memory=(ResidentMemoryEntry(path=_DOMAIN_MODEL_PATH, summary="domain model"),),
    )


def _action_from_workstream(workstream: ResidentWorkstream) -> ResidentActionCandidate:
    return ResidentActionCandidate(
        title=workstream.title,
        action=workstream.purpose,
        reason=workstream.serves,
        source="resident_workstream",
        risk_boundaries=workstream.risk_boundaries,
        required_capabilities=workstream.required_capabilities,
    )


def _artifact_from_result(
    workstream: ResidentWorkstream,
    result: WorkstreamExecutionResult,
) -> ExpertArtifact:
    path = result.artifact_refs[0] if result.artifact_refs else ""
    return ExpertArtifact(
        title=f"{workstream.title} artifact",
        kind=workstream.selected_work_product,
        path=path,
        purpose=workstream.purpose,
        summary=result.summary,
    )


def _extract_artifact_refs(text: str, fields: dict[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for value in _walk_values(fields):
        refs.extend(match.group("path") for match in _PATH_RE.finditer(str(value)))
    refs.extend(match.group("path") for match in _PATH_RE.finditer(text))
    return tuple(dict.fromkeys(ref for ref in refs if _looks_like_artifact_ref(ref)))


def _render_domain_model(model: ResidentDomainModel) -> str:
    return (
        "# Resident Domain Model\n\n"
        f"- updated_at: {model.updated_at.isoformat()}\n"
        f"- mandate: {model.mandate}\n\n"
        "## Current Understanding\n\n"
        f"{model.current_understanding or 'Unknown.'}\n\n"
        f"## Hypotheses\n\n{_render_list(model.hypotheses)}\n\n"
        f"## Known Facts\n\n{_render_list(model.known_facts)}\n\n"
        f"## Open Questions\n\n{_render_list(model.open_questions)}\n\n"
        f"## Domain Risks\n\n{_render_list(model.domain_risks)}\n\n"
        f"## Opportunities\n\n{_render_list(model.opportunities)}\n\n"
        f"## Capability Gaps\n\n{_render_list(model.capability_gaps)}\n\n"
        "## Active Workstreams\n\n"
        f"{_render_list(_workstream_line(item) for item in model.active_workstreams)}\n\n"
        f"## Recent Outcomes\n\n{_render_list(model.recent_outcomes)}\n\n"
        "## Learned Policy Observations\n\n"
        f"{_render_list(_policy_observation_lines(model))}\n\n"
        "## Artifacts\n\n"
        f"{_render_list(f'{item.kind}: {item.path} — {item.title}' for item in model.artifacts)}\n"
    )


def _render_workstream(workstream: ResidentWorkstream) -> str:
    return (
        f"# {workstream.title}\n\n"
        f"- id: {workstream.id}\n"
        f"- status: {workstream.status}\n"
        f"- kind: {workstream.kind}\n"
        f"- selected_work_product: {workstream.selected_work_product}\n"
        f"- budget_estimate: {workstream.budget_estimate}\n"
        f"- parent_domain_model_ref: {workstream.parent_domain_model_ref}\n"
        f"- updated_at: {workstream.updated_at.isoformat()}\n\n"
        "## Purpose\n\n"
        f"{workstream.purpose}\n\n"
        "## Serves\n\n"
        f"{workstream.serves}\n\n"
        "## Expected Artifact\n\n"
        f"{workstream.expected_artifact}\n\n"
        f"## Required Capabilities\n\n{_render_list(workstream.required_capabilities)}\n\n"
        f"## Risk Boundaries\n\n{_render_list(workstream.risk_boundaries)}\n"
    )


def _workstream_line(workstream: ResidentWorkstream) -> str:
    return f"{workstream.id}: {workstream.title} [{workstream.status}]"


def _render_execution_artifact(
    model: ResidentDomainModel,
    workstream: ResidentWorkstream,
    result: WorkstreamExecutionResult,
) -> str:
    return (
        f"# {workstream.title} Artifact\n\n"
        f"- kind: {workstream.selected_work_product}\n"
        f"- workstream_id: {workstream.id}\n"
        f"- status: {result.status}\n"
        f"- produced_at: {datetime.now(UTC).isoformat()}\n\n"
        "## Purpose\n\n"
        f"{workstream.purpose}\n\n"
        "## Summary\n\n"
        f"{result.summary}\n\n"
        f"## Artifact References\n\n{_render_list(result.artifact_refs)}\n\n"
        f"## Facts\n\n{_render_list(result.facts)}\n\n"
        f"## Lessons\n\n{_render_list(result.lessons)}\n\n"
        "## Domain Context\n\n"
        f"{model.current_understanding or 'Unknown.'}\n"
    )


def _render_consolidation(
    model: ResidentDomainModel,
    result: WorkstreamExecutionResult,
) -> str:
    return (
        f"# Workstream Consolidation: {result.workstream_id}\n\n"
        f"- status: {result.status}\n"
        f"- updated_at: {model.updated_at.isoformat()}\n\n"
        f"## Summary\n\n{result.summary}\n\n"
        f"## Facts\n\n{_render_list(result.facts)}\n\n"
        f"## Capability Gaps\n\n{_render_list(result.capability_gaps)}\n"
    )


def _parse_domain_model_page(content: str, *, mandate: str) -> ResidentDomainModel:
    policy_observations = tuple(
        item
        for item in (
            _parse_policy_observation_line(line)
            for line in _section_items(content, "Learned Policy Observations")
        )
        if item is not None
    )
    artifacts = tuple(
        item
        for item in (_parse_artifact_line(line) for line in _section_items(content, "Artifacts"))
        if item is not None
    )
    return ResidentDomainModel(
        mandate=mandate,
        current_understanding=_section(content, "Current Understanding"),
        hypotheses=tuple(_section_items(content, "Hypotheses")),
        known_facts=tuple(_section_items(content, "Known Facts")),
        open_questions=tuple(_section_items(content, "Open Questions")),
        domain_risks=tuple(_section_items(content, "Domain Risks")),
        opportunities=tuple(_section_items(content, "Opportunities")),
        capability_gaps=tuple(_section_items(content, "Capability Gaps")),
        recent_outcomes=tuple(_section_items(content, "Recent Outcomes")),
        learned_policy_observations=policy_observations,
        artifacts=artifacts,
    )


def _parse_workstream_page(content: str) -> ResidentWorkstream | None:
    title = ""
    for line in content.splitlines():
        if line.startswith("# "):
            title = line.removeprefix("# ").strip()
            break
    metadata = _metadata(content)
    workstream_id = metadata.get("id") or _slug(title)
    if not title or not workstream_id:
        return None
    return ResidentWorkstream(
        id=workstream_id,
        title=title,
        purpose=_section(content, "Purpose"),
        serves=_section(content, "Serves"),
        expected_artifact=_section(content, "Expected Artifact"),
        kind=metadata.get("kind") or ResidentWorkstreamKind.LOCAL_FOLLOW_UP.value,
        status=metadata.get("status") or ResidentWorkstreamStatus.PROPOSED.value,
        parent_domain_model_ref=metadata.get("parent_domain_model_ref") or "",
        selected_work_product=metadata.get("selected_work_product")
        or WorkProductKind.DOMAIN_BRIEF.value,
        budget_estimate=metadata.get("budget_estimate") or "small",
        required_capabilities=tuple(_section_items(content, "Required Capabilities")),
        risk_boundaries=tuple(_section_items(content, "Risk Boundaries")),
    )


def _metadata(content: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in content.splitlines():
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        data[key.strip()] = value.strip()
    return data


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


def _string_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    if not text:
        return ()
    if text.startswith(("[", "{")):
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError:
            parsed = None
        if parsed is not None and parsed is not value:
            return _string_items(parsed)
    return (text.strip("\"'"),)


def _outcome_items(fields: dict[str, Any], record: Any, name: str) -> tuple[str, ...]:
    parsed_items = _string_items(fields.get(name))
    raw_items = _extract_outcome_list_items(getattr(record, "response", ""), name)
    if raw_items and (_looks_malformed_items(parsed_items) or len(raw_items) > len(parsed_items)):
        return raw_items
    return parsed_items


def _looks_malformed_items(items: tuple[str, ...]) -> bool:
    return any(item.startswith(("[", "{")) or item.endswith(('"', ",")) for item in items)


def _extract_outcome_list_items(text: str, name: str) -> tuple[str, ...]:
    field_pattern = re.compile(rf"(?m)^{re.escape(name)}\s*:\s*")
    match = field_pattern.search(text)
    if match is None:
        return ()
    rest = text[match.end() :]
    next_field = re.search(r"(?m)^[A-Za-z_][A-Za-z0-9_]*\s*:", rest)
    raw = rest[: next_field.start()] if next_field is not None else rest
    raw = raw.strip()
    if not raw.startswith("["):
        return ()
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return ()
    return _string_items(parsed)


def _looks_like_artifact_ref(ref: str) -> bool:
    if not ref or ref.startswith(("http", "/", "-")):
        return False
    if "/" not in ref and "." not in ref:
        return False
    if ref.count(".") == 1 and "/" not in ref and len(ref.split(".", 1)[0]) < 4:
        return False
    return True


def _selected_action_items(fields: dict[str, Any]) -> tuple[str, ...]:
    raw = fields.get("selected_next_action")
    if isinstance(raw, dict):
        text = str(raw.get("action") or raw.get("title") or "").strip()
        return (text,) if text else ()
    return _string_items(raw)


def _risk_like_items(items: tuple[str, ...]) -> tuple[str, ...]:
    terms = ("risk", "approval", "spending", "physical", "external", "credential", "unknown")
    return tuple(item for item in items if _has_any(item.casefold(), terms))


def _merge_items(*groups: Any, max_items: int = 20) -> tuple[str, ...]:
    values: list[str] = []
    for group in groups:
        for item in _string_items(group):
            cleaned = _compact_line(item, limit=240)
            if cleaned and cleaned not in values:
                values.append(cleaned)
            if len(values) >= max_items:
                return tuple(values)
    return tuple(values)


def _merge_artifacts(
    *groups: tuple[ExpertArtifact, ...],
    max_items: int = 20,
) -> tuple[ExpertArtifact, ...]:
    values: list[ExpertArtifact] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = item.path or item.title
            if key in seen:
                continue
            values.append(item)
            seen.add(key)
            if len(values) >= max_items:
                return tuple(values)
    return tuple(values)


def _merge_policy_observations(
    *groups: tuple[ResidentPolicyObservation, ...],
    max_items: int = 20,
) -> tuple[ResidentPolicyObservation, ...]:
    values: list[ResidentPolicyObservation] = []
    seen: set[tuple[str, str, str]] = set()
    for group in groups:
        for item in group:
            key = (item.subject, item.source, item.observation)
            if key in seen:
                continue
            values.append(item)
            seen.add(key)
            if len(values) >= max_items:
                return tuple(values)
    return tuple(values)


def _policy_observation_lines(model: ResidentDomainModel) -> tuple[str, ...]:
    return tuple(_policy_observation_line(item) for item in model.learned_policy_observations)


def _policy_observation_line(observation: ResidentPolicyObservation) -> str:
    compact = _compact_line(observation.observation, limit=180)
    return f"{observation.subject} | {observation.status} | {observation.source} | {compact}"


def _parse_policy_observation_line(line: str) -> ResidentPolicyObservation | None:
    parts = [part.strip() for part in line.split("|", 3)]
    if len(parts) != 4 or not parts[0]:
        return None
    return ResidentPolicyObservation(
        subject=parts[0],
        status=parts[1] or "candidate",
        source=parts[2] or "resident_domain_model",
        observation=parts[3],
    )


def _parse_artifact_line(line: str) -> ExpertArtifact | None:
    kind, sep, remainder = line.partition(":")
    if not sep:
        return None
    path, title_sep, title = remainder.strip().partition(" — ")
    return ExpertArtifact(
        title=title.strip() if title_sep else path.strip(),
        kind=kind.strip() or WorkProductKind.DOMAIN_BRIEF.value,
        path=path.strip(),
        purpose="Recovered from resident domain model.",
    )


def _walk_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        values: list[Any] = []
        for child in value.values():
            values.extend(_walk_values(child))
        return values
    if isinstance(value, (list, tuple, set)):
        values = []
        for child in value:
            values.extend(_walk_values(child))
        return values
    return [value]


def _clean_title(value: str) -> str:
    text = _compact_line(value, limit=90)
    text = text.strip("\"'[] ")
    if ":" in text and len(text.split(":", 1)[0]) < 24:
        text = text.split(":", 1)[1].strip()
    return text or "Resident workstream"


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _required_capabilities_for_kind(kind: ResidentWorkstreamKind) -> tuple[str, ...]:
    if kind == ResidentWorkstreamKind.REMOTE_EXECUTION:
        return ("remote_execution",)
    if kind == ResidentWorkstreamKind.WORKFLOW_BUILDING:
        return ("workflow_tools",)
    if kind == ResidentWorkstreamKind.RESEARCH:
        return ("research",)
    if kind == ResidentWorkstreamKind.IMPLEMENTATION:
        return ("code",)
    return ()
