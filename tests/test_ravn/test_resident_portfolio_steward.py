from __future__ import annotations

from pathlib import Path

import pytest

from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import ResidentBudgetSnapshot
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentPortfolioStewardActionKind,
)
from ravn.domain.wakeful_resident import (
    WakefulResidentCycleRecord,
    WakefulResidentDecisionKind,
    WakefulResidentRun,
)
from ravn.resident_portfolio import (
    LocalResidentWorkItemBackend,
    ResidentPortfolioStewardConfig,
    ResidentPortfolioStewardRuntime,
    prioritize_objectives,
    select_objectives,
)

MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts. "
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


class TrackingBackend(LocalResidentWorkItemBackend):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.events: list[str] = []

    async def read_portfolio(self, mandate: str) -> ResidentPortfolio | None:
        self.events.append("read_portfolio")
        return await super().read_portfolio(mandate)

    async def list_objectives(self, mandate: str) -> list[ResidentObjective]:
        self.events.append("list_objectives")
        return await super().list_objectives(mandate)

    async def write_objective(self, objective: ResidentObjective) -> str:
        self.events.append(f"write_objective:{objective.id}:{objective.status}")
        return await super().write_objective(objective)

    async def append_decision(self, mandate: str, entry: str) -> str:
        self.events.append(f"append_decision:{entry}")
        return await super().append_decision(mandate, entry)


class FakeWakeRuntime:
    def __init__(
        self,
        *,
        decision: WakefulResidentDecisionKind = WakefulResidentDecisionKind.STOP,
        finding_summaries: tuple[str, ...] = (
            "missing capability: resident needs a generic proof reviewer",
        ),
        artifact_refs: tuple[str, ...] = ("resident/domain-expert/artifacts/steward-proof.md",),
    ) -> None:
        self.calls: list[str] = []
        self.decision = decision
        self.finding_summaries = finding_summaries
        self.artifact_refs = artifact_refs
        self.events_before_call: list[str] = []

    async def run(self, mandate: str) -> WakefulResidentRun:
        self.calls.append(mandate)
        cycle = WakefulResidentCycleRecord(
            cycle_number=1,
            mandate=mandate,
            prior_domain_model_ref="resident/domain-expert/domain-model.md",
            attention_reason="portfolio steward selected work",
            selected_action="advance selected objective",
            work_created_or_advanced=("selected objective advanced",),
            artifact_refs=self.artifact_refs,
            finding_summaries=self.finding_summaries,
            decision=self.decision,
            decision_reason="What judgment should guide the next pass?"
            if self.decision == WakefulResidentDecisionKind.ASK_OPERATOR
            else "max turns reached: 1",
            budget=ResidentBudgetSnapshot(
                turns_used=1,
                usage=TokenUsage(input_tokens=11, output_tokens=13),
            ),
        )
        return WakefulResidentRun(
            mandate=mandate,
            cycles=(cycle,),
            final_decision=self.decision,
            final_reason=cycle.decision_reason,
            budget=cycle.budget,
        )


class ObservingWakeRuntime(FakeWakeRuntime):
    def __init__(self, backend: TrackingBackend) -> None:
        super().__init__()
        self.backend = backend

    async def run(self, mandate: str) -> WakefulResidentRun:
        self.events_before_call = list(self.backend.events)
        return await super().run(mandate)


def _objective(
    objective_id: str,
    title: str,
    *,
    status: str = ResidentObjectiveStatus.CANDIDATE.value,
    dependencies: tuple[str, ...] = (),
    kind: str = ResidentObjectiveKind.RESEARCH.value,
    proof_progress: tuple[str, ...] = (),
    pending_question: str = "",
    source_evidence: tuple[str, ...] = ("remembered evidence",),
    reasoning: str = "remembered reason",
    proof_criteria: tuple[str, ...] = ("proof exists",),
) -> ResidentObjective:
    return ResidentObjective(
        id=objective_id,
        title=title,
        purpose=f"Advance {title}",
        serves_mandate_because="It advances the resident mandate.",
        expected_outcome="A proof artifact exists.",
        proof_criteria=proof_criteria,
        kind=kind,
        dependencies=dependencies,
        status=status,
        pending_question=pending_question,
        source_evidence=source_evidence,
        reasoning=reasoning,
        proof_progress=proof_progress,
    )


async def _write_portfolio(
    backend: LocalResidentWorkItemBackend,
    *objectives: ResidentObjective,
) -> None:
    for objective in objectives:
        await backend.write_objective(objective)
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE, objectives=objectives))


@pytest.mark.asyncio
async def test_steward_uses_work_item_backend_and_validates_before_advancement(
    tmp_path: Path,
) -> None:
    backend = TrackingBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective("ready", "Ready objective", kind=ResidentObjectiveKind.TOOL_BUILDING.value),
    )
    wake = ObservingWakeRuntime(backend)

    await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=wake,
        config=ResidentPortfolioStewardConfig(max_passes=1),
    ).run(MANDATE)

    assert wake.calls
    assert "read_portfolio" in wake.events_before_call
    assert "list_objectives" in wake.events_before_call
    assert any(event.startswith("write_objective:ready:active") for event in backend.events)


@pytest.mark.asyncio
async def test_safe_resume_repair_is_audited(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective(
            "paused",
            "Paused objective",
            status=ResidentObjectiveStatus.PAUSED.value,
            source_evidence=("prior run says this still matters",),
            reasoning="",
        ),
    )

    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=FakeWakeRuntime(),
        config=ResidentPortfolioStewardConfig(max_passes=1),
    ).run(MANDATE)
    restored = (await backend.list_objectives(MANDATE))[0]

    assert run.passes[0].action_taken == ResidentPortfolioStewardActionKind.REPAIR
    assert run.passes[0].repairs_attempted[0].code == "resume_reason_missing"
    assert "prior run says this still matters" in restored.reasoning


@pytest.mark.asyncio
async def test_unsafe_repairs_are_skipped_and_completed_proof_is_not_faked(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    completed = _objective(
        "done",
        "Done without proof",
        status=ResidentObjectiveStatus.COMPLETED.value,
        proof_progress=(),
    )
    ready = _objective("ready", "Ready objective")
    await _write_portfolio(backend, completed, ready)

    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=FakeWakeRuntime(),
        config=ResidentPortfolioStewardConfig(max_passes=1),
    ).run(MANDATE)
    restored = {item.id: item for item in await backend.list_objectives(MANDATE)}

    assert any(
        item.code == "completed_without_proof" and "cannot invent proof" in item.reason
        for item in run.passes[0].repairs_skipped
    )
    assert restored["done"].proof_progress == ()


@pytest.mark.asyncio
async def test_missing_dependency_is_marked_blocked(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective("blocked", "Blocked objective", dependencies=("missing",)),
    )

    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=FakeWakeRuntime(),
        config=ResidentPortfolioStewardConfig(max_passes=1),
    ).run(MANDATE)
    restored = (await backend.list_objectives(MANDATE))[0]

    assert run.passes[0].repairs_attempted[0].code == "missing_dependency"
    assert restored.status == ResidentObjectiveStatus.BLOCKED.value


@pytest.mark.asyncio
async def test_needs_operator_objective_is_not_advanced_directly(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective(
            "ask",
            "Ask operator",
            status=ResidentObjectiveStatus.NEEDS_OPERATOR.value,
            pending_question="May I continue?",
            kind=ResidentObjectiveKind.OPERATOR_QUESTION.value,
        ),
    )
    wake = FakeWakeRuntime()

    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=wake,
        config=ResidentPortfolioStewardConfig(max_passes=1),
    ).run(MANDATE)

    assert run.passes[0].action_taken == ResidentPortfolioStewardActionKind.ASK_OPERATOR
    assert wake.calls == []


@pytest.mark.asyncio
async def test_operator_question_is_persisted_when_judgment_is_needed(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective(
            "ask",
            "Ask operator",
            status=ResidentObjectiveStatus.NEEDS_OPERATOR.value,
            kind=ResidentObjectiveKind.OPERATOR_QUESTION.value,
            pending_question="",
            reasoning="",
        ),
    )

    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=FakeWakeRuntime(),
        config=ResidentPortfolioStewardConfig(max_passes=1),
    ).run(MANDATE)
    restored = (await backend.list_objectives(MANDATE))[0]

    assert run.passes[0].repairs_attempted[0].code == "operator_question_missing"
    assert restored.pending_question


@pytest.mark.asyncio
async def test_selected_objective_matches_prioritization_after_repair(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    paused = _objective(
        "paused",
        "Paused objective",
        status=ResidentObjectiveStatus.PAUSED.value,
        source_evidence=("resume this",),
        reasoning="",
    )
    ready = _objective(
        "ready-tool",
        "Ready tool objective",
        kind=ResidentObjectiveKind.TOOL_BUILDING.value,
    )
    await _write_portfolio(backend, paused, ready)

    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=FakeWakeRuntime(),
        config=ResidentPortfolioStewardConfig(max_passes=2, max_advancements=0),
    ).run(MANDATE)
    objectives = tuple(await backend.list_objectives(MANDATE))
    expected = select_objectives(
        prioritize_objectives(objectives, mandate=MANDATE),
        max_selected=1,
        max_active=3,
    )[0]

    assert run.passes[1].selected_objective is not None
    assert run.passes[1].selected_objective.objective_id == expected.id


@pytest.mark.asyncio
async def test_multi_pass_loop_stops_at_configured_bounds(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective(
            "paused",
            "Paused objective",
            status=ResidentObjectiveStatus.PAUSED.value,
            source_evidence=("resume this",),
            reasoning="",
        ),
    )

    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=FakeWakeRuntime(),
        config=ResidentPortfolioStewardConfig(max_passes=2, max_advancements=2),
    ).run(MANDATE)

    assert len(run.passes) == 2
    assert run.final_action == ResidentPortfolioStewardActionKind.STOP
    assert "pass limit" in run.final_suggested_next_action


@pytest.mark.asyncio
async def test_advancement_updates_objective_evidence(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("ready", "Ready objective"))

    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=FakeWakeRuntime(),
        config=ResidentPortfolioStewardConfig(max_passes=1),
    ).run(MANDATE)
    restored = {item.id: item for item in await backend.list_objectives(MANDATE)}

    assert run.passes[0].action_taken == ResidentPortfolioStewardActionKind.ADVANCE
    assert restored["ready"].proof_progress
    assert restored["ready"].artifact_links


@pytest.mark.asyncio
async def test_follow_up_objectives_are_created_from_generic_evidence(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("ready", "Ready objective"))

    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=FakeWakeRuntime(
            finding_summaries=(
                "opportunity: compare two future approaches",
                "missing capability: generic execution review",
            )
        ),
        config=ResidentPortfolioStewardConfig(max_passes=1, max_follow_up_objectives=2),
    ).run(MANDATE)
    stored_ids = {item.id for item in await backend.list_objectives(MANDATE)}

    assert len(run.passes[0].new_follow_up_objectives) == 2
    assert any(item.startswith("follow-up") for item in stored_ids)


@pytest.mark.asyncio
async def test_steward_surfaces_capability_gap_as_discovery_objective(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("ready", "Ready objective"))

    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=FakeWakeRuntime(
            finding_summaries=("missing capability: generic adapter review",)
        ),
        config=ResidentPortfolioStewardConfig(max_passes=1),
    ).run(MANDATE)

    assert any(
        item.title.startswith("Discover capability path:")
        for item in run.passes[0].new_follow_up_objectives
    )


@pytest.mark.asyncio
async def test_wake_operator_decision_creates_operator_follow_up(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("ready", "Ready objective"))

    run = await ResidentPortfolioStewardRuntime(
        backend=backend,
        wake_runtime=FakeWakeRuntime(decision=WakefulResidentDecisionKind.ASK_OPERATOR),
        config=ResidentPortfolioStewardConfig(max_passes=1),
    ).run(MANDATE)
    stored = await backend.list_objectives(MANDATE)

    assert run.passes[0].operator_questions
    assert any(item.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value for item in stored)


def test_resident_portfolio_steward_contains_no_domain_specific_playbook_terms() -> None:
    source = Path("src/ravn/resident_portfolio.py").read_text(encoding="utf-8").casefold()

    for forbidden in (
        "kanuck",
        "inventory",
        "3d printing",
        "prd",
        "srd",
        "blender",
        "slicing",
        "product catalog",
    ):
        assert forbidden not in source
