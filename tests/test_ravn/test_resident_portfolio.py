from __future__ import annotations

from pathlib import Path

import pytest

from ravn.adapters.resident_work.local import LocalResidentWorkItemBackend
from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import ResidentBudgetSnapshot
from ravn.domain.resident_expert import (
    ResidentDomainModel,
)
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentPortfolioDecisionKind,
)
from ravn.domain.wakeful_resident import (
    WakefulResidentCycleRecord,
    WakefulResidentDecisionKind,
    WakefulResidentRun,
)
from ravn.resident_expert import LocalResidentDomainExpertMemory
from ravn.resident_portfolio import (
    ResidentLongHorizonWorkManager,
    ResidentPortfolioConfig,
    ResidentPortfolioEvidence,
    discover_objectives,
    merge_objectives,
    prioritize_objectives,
    select_objectives,
)
from ravn.wakeful_resident import LocalWakefulResidentMemory

MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts. "
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


class FakeWakeRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, mandate: str) -> WakefulResidentRun:
        self.calls.append(mandate)
        cycle = WakefulResidentCycleRecord(
            cycle_number=1,
            mandate=mandate,
            prior_domain_model_ref="resident/domain-expert/domain-model.md",
            attention_reason="selected objective deserves bounded work",
            selected_action="advance objective",
            work_created_or_advanced=("objective-workstream: created [completed]",),
            artifact_refs=("resident/domain-expert/artifacts/objective.md",),
            finding_summaries=("Objective advanced with proof artifact.",),
            decision=WakefulResidentDecisionKind.STOP,
            decision_reason="max turns reached: 1",
            budget=ResidentBudgetSnapshot(
                turns_used=1,
                usage=TokenUsage(input_tokens=5, output_tokens=7),
            ),
        )
        return WakefulResidentRun(
            mandate=mandate,
            cycles=(cycle,),
            final_decision=WakefulResidentDecisionKind.STOP,
            final_reason="max turns reached: 1",
            budget=cycle.budget,
        )


class BootstrapWakeRuntime:
    def __init__(
        self,
        *,
        expert_memory: LocalResidentDomainExpertMemory,
        wake_memory: LocalWakefulResidentMemory,
    ) -> None:
        self.expert_memory = expert_memory
        self.wake_memory = wake_memory
        self.calls: list[str] = []

    async def run(self, mandate: str) -> WakefulResidentRun:
        self.calls.append(mandate)
        if len(self.calls) == 1:
            await self.expert_memory.write_domain_model(
                ResidentDomainModel(
                    mandate=MANDATE,
                    current_understanding="The resident has oriented from a mandate.",
                    opportunities=("Create a compact resident operating baseline.",),
                    capability_gaps=("No durable resident work portfolio exists yet.",),
                    recent_outcomes=("Mandate-only orientation produced useful work.",),
                )
            )
            cycle = WakefulResidentCycleRecord(
                cycle_number=1,
                mandate=mandate,
                prior_domain_model_ref="",
                attention_reason="no domain model exists; orient from the resident mandate",
                selected_action="orient from mandate",
                work_created_or_advanced=("orientation: created [completed]",),
                artifact_refs=("resident/domain-expert/artifacts/orientation.md",),
                finding_summaries=("Mandate-only orientation created portfolio evidence.",),
                decision=WakefulResidentDecisionKind.STOP,
                decision_reason="max turns reached: 1",
                budget=ResidentBudgetSnapshot(
                    turns_used=1,
                    usage=TokenUsage(input_tokens=5, output_tokens=7),
                ),
            )
            await self.wake_memory.write_wake_record(cycle)
        else:
            cycle = WakefulResidentCycleRecord(
                cycle_number=1,
                mandate=mandate,
                prior_domain_model_ref="resident/domain-expert/domain-model.md",
                attention_reason="selected objective deserves bounded work",
                selected_action="advance objective",
                work_created_or_advanced=("objective-workstream: created [completed]",),
                artifact_refs=("resident/domain-expert/artifacts/objective.md",),
                finding_summaries=("Objective advanced after mandate-only bootstrap.",),
                decision=WakefulResidentDecisionKind.STOP,
                decision_reason="max turns reached: 1",
                budget=ResidentBudgetSnapshot(
                    turns_used=1,
                    usage=TokenUsage(input_tokens=11, output_tokens=13),
                ),
            )
        return WakefulResidentRun(
            mandate=mandate,
            cycles=(cycle,),
            final_decision=WakefulResidentDecisionKind.STOP,
            final_reason="max turns reached: 1",
            budget=cycle.budget,
        )


def _objective(
    objective_id: str,
    title: str,
    *,
    status: str = ResidentObjectiveStatus.CANDIDATE.value,
    dependencies: tuple[str, ...] = (),
    kind: str = ResidentObjectiveKind.RESEARCH.value,
    priority_score: int = 0,
    pending_question: str = "",
) -> ResidentObjective:
    return ResidentObjective(
        id=objective_id,
        title=title,
        purpose=f"Advance {title}",
        serves_mandate_because="It advances the resident mandate.",
        expected_outcome="A proof artifact exists.",
        proof_criteria=("A proof artifact exists.",),
        kind=kind,
        dependencies=dependencies,
        status=status,
        priority_score=priority_score,
        pending_question=pending_question,
        source_evidence=(title,),
        reasoning="test evidence",
    )


def _model() -> ResidentDomainModel:
    return ResidentDomainModel(
        mandate=MANDATE,
        current_understanding="Domain expert and wakeful runtime are done.",
        open_questions=("Which objective should reduce autonomy uncertainty next?",),
        opportunities=("Improve resident self-direction from remembered work.",),
        capability_gaps=(
            "No long-horizon portfolio backend exists for resident work items.",
            "Prioritization across competing resident objectives is missing.",
        ),
        recent_outcomes=("Resident Domain Expert Loop V0 complete.",),
    )


@pytest.mark.asyncio
async def test_portfolio_is_persisted_and_read_back(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    objective = _objective("obj", "Create resident portfolio")
    portfolio = ResidentPortfolio(
        mandate=MANDATE,
        objectives=(objective,),
        decision_history=("selected obj because proof value is high",),
    )

    ref = await backend.write_portfolio(portfolio)
    await backend.write_objective(objective)
    restored = await backend.read_portfolio(MANDATE)
    objectives = await backend.list_objectives(MANDATE)

    assert ref == "resident/portfolio/portfolio.md"
    assert restored is not None
    assert restored.decision_history == ("selected obj because proof value is high",)
    assert objectives[0].id == "obj"


def test_candidate_objectives_are_derived_from_state_gaps_and_evidence() -> None:
    portfolio = ResidentPortfolio(mandate=MANDATE)
    evidence = ResidentPortfolioEvidence(
        domain_model=_model(),
        artifact_refs=("resident/domain-expert/artifacts/wakeful-proof.md",),
    )

    discovered = discover_objectives(MANDATE, portfolio=portfolio, evidence=evidence)

    titles = {objective.title for objective in discovered}
    assert any("portfolio backend" in title for title in titles)
    assert any("Prioritization" in title for title in titles)
    assert any("Review artifact" in title for title in titles)


def test_completed_milestones_influence_objective_selection() -> None:
    completed = _objective(
        "wakeful-runtime",
        "Wakeful Resident Runtime V0",
        status=ResidentObjectiveStatus.COMPLETED.value,
    )
    portfolio = ResidentPortfolio(mandate=MANDATE, objectives=(completed,))

    discovered = discover_objectives(
        MANDATE,
        portfolio=portfolio,
        evidence=ResidentPortfolioEvidence(domain_model=_model()),
    )

    assert any("Build on completed milestone" in objective.title for objective in discovered)


def test_prioritization_prefers_ready_high_leverage_work_over_blocked_work() -> None:
    ready = _objective(
        "ready",
        "Close capability gap: backend adapter missing",
        kind=ResidentObjectiveKind.TOOL_BUILDING.value,
    )
    blocked = _objective(
        "blocked",
        "Implement dependent work",
        dependencies=("missing",),
        kind=ResidentObjectiveKind.IMPLEMENTATION.value,
    )

    prioritized = prioritize_objectives((blocked, ready), mandate=MANDATE)

    assert prioritized[0].id == "ready"
    assert prioritized[0].priority_score > prioritized[1].priority_score


def test_dependencies_prevent_premature_advancement() -> None:
    blocked = prioritize_objectives(
        (_objective("blocked", "Blocked objective", dependencies=("not-done",)),),
        mandate=MANDATE,
    )

    assert select_objectives(blocked, max_selected=1, max_active=3) == ()


def test_paused_objectives_can_resume_when_ready() -> None:
    paused = prioritize_objectives(
        (
            _objective(
                "paused",
                "Resume paused work",
                status=ResidentObjectiveStatus.PAUSED.value,
            ),
        ),
        mandate=MANDATE,
    )

    selected = select_objectives(paused, max_selected=1, max_active=3)

    assert selected[0].id == "paused"


def test_duplicate_objectives_are_superseded_into_one_record() -> None:
    first = _objective("same", "Close capability gap")
    second = _objective(
        "same",
        "Close capability gap",
        status=ResidentObjectiveStatus.ACTIVE.value,
    )

    merged = merge_objectives((first, second))

    assert len(merged) == 1
    assert merged[0].status == ResidentObjectiveStatus.ACTIVE.value
    assert "same" in merged[0].supersedes


@pytest.mark.asyncio
async def test_operator_needed_objectives_route_without_repeated_wake_calls(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(
        _objective(
            "ask",
            "Ask operator for approval",
            status=ResidentObjectiveStatus.NEEDS_OPERATOR.value,
            pending_question="May I proceed?",
        )
    )
    wake = FakeWakeRuntime()
    manager = ResidentLongHorizonWorkManager(
        backend=backend,
        wake_runtime=wake,
        config=ResidentPortfolioConfig(max_objectives_selected=1),
    )

    run = await manager.run(MANDATE)

    assert run.decision == ResidentPortfolioDecisionKind.ASK_OPERATOR
    assert run.decision_reason == "May I proceed?"
    assert wake.calls == []


@pytest.mark.asyncio
async def test_selected_objective_hands_off_to_wakeful_runtime_and_updates_links(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    expert_memory = LocalResidentDomainExpertMemory(tmp_path)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    await expert_memory.write_domain_model(_model())
    wake_record = WakefulResidentCycleRecord(
        cycle_number=1,
        mandate=MANDATE,
        prior_domain_model_ref="resident/domain-expert/domain-model.md",
        attention_reason="prior state",
        selected_action="prior",
        work_created_or_advanced=("prior: completed",),
        artifact_refs=("resident/domain-expert/artifacts/prior.md",),
        finding_summaries=("prior finding",),
        decision=WakefulResidentDecisionKind.STOP,
        decision_reason="max turns reached: 1",
        budget=ResidentBudgetSnapshot(turns_used=1),
    )
    await wake_memory.write_wake_record(wake_record)
    wake = FakeWakeRuntime()
    manager = ResidentLongHorizonWorkManager(
        backend=backend,
        wake_runtime=wake,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=ResidentPortfolioConfig(max_objectives_selected=1),
    )

    run = await manager.run(MANDATE)

    assert wake.calls
    assert "Resident portfolio selected this long-horizon objective" in wake.calls[0]
    assert run.advanced_objectives
    assert run.advanced_objectives[0].artifact_links
    assert run.advanced_objectives[0].proof_progress


@pytest.mark.asyncio
async def test_empty_portfolio_bootstraps_from_mandate_then_advances_objective(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    expert_memory = LocalResidentDomainExpertMemory(tmp_path)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    wake = BootstrapWakeRuntime(expert_memory=expert_memory, wake_memory=wake_memory)
    manager = ResidentLongHorizonWorkManager(
        backend=backend,
        wake_runtime=wake,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=ResidentPortfolioConfig(max_objectives_selected=1),
    )

    run = await manager.run(MANDATE)
    restored = await backend.read_portfolio(MANDATE)

    assert len(wake.calls) == 2
    assert wake.calls[0] == MANDATE
    assert "Resident portfolio selected this long-horizon objective" in wake.calls[1]
    assert len(run.discovered_objectives) >= 2
    assert run.advanced_objectives
    assert run.advanced_objectives[0].status == ResidentObjectiveStatus.COMPLETED.value
    assert restored is not None
    assert any("bootstrap wakeful orientation" in item for item in restored.decision_history)


@pytest.mark.asyncio
async def test_budget_limits_stop_advancement_without_losing_state(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await backend.write_objective(_objective("ready", "Ready objective"))
    manager = ResidentLongHorizonWorkManager(
        backend=backend,
        wake_runtime=FakeWakeRuntime(),
        config=ResidentPortfolioConfig(max_objectives_selected=1),
    )

    run = await manager.run(MANDATE)
    restored = await backend.read_portfolio(MANDATE)

    assert run.decision == ResidentPortfolioDecisionKind.STOP
    assert run.decision_reason == "max turns reached: 1"
    assert restored is not None
    assert restored.objectives


def test_resident_portfolio_contains_no_domain_specific_playbook_terms() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("src/ravn/resident_portfolio").glob("*.py"))
    ).casefold()

    for forbidden in (
        "kanuck",
        "inventory",
        "3d printing",
        "forge",
        "prd",
        "srd",
        "blender",
        "slicing",
        "product catalog",
    ):
        assert forbidden not in source
