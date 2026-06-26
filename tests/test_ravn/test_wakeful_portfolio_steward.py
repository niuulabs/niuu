from __future__ import annotations

from pathlib import Path

import pytest

from ravn.adapters.resident_work.local import LocalResidentWorkItemBackend
from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import ResidentBudgetSnapshot
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentPortfolioStewardActionKind,
    ResidentPortfolioStewardRun,
)
from ravn.domain.wakeful_resident import (
    WakefulPortfolioStewardActionKind,
)
from ravn.resident_portfolio import (
    ResidentPortfolioStewardConfig,
    ResidentPortfolioStewardRuntime,
    ResidentPortfolioValidator,
)
from ravn.wakeful_resident import (
    LocalWakefulPortfolioStewardMemory,
    WakefulPortfolioStewardConfig,
    WakefulPortfolioStewardRuntime,
    derive_portfolio_steward_attention_reason,
)

MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts. "
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


class FakeSteward:
    def __init__(
        self,
        run: ResidentPortfolioStewardRun | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.run_result = run or ResidentPortfolioStewardRun(
            mandate=MANDATE,
            passes=(),
            final_action=ResidentPortfolioStewardActionKind.SLEEP,
            final_suggested_next_action="fake steward slept",
        )

    async def run(self, mandate: str) -> ResidentPortfolioStewardRun:
        self.calls.append(mandate)
        return self.run_result


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


def _objective(
    objective_id: str,
    title: str,
    *,
    status: str = ResidentObjectiveStatus.CANDIDATE.value,
    kind: str = ResidentObjectiveKind.RESEARCH.value,
    pending_question: str = "",
    reasoning: str = "remembered reason",
    source_evidence: tuple[str, ...] = ("remembered evidence",),
    proof_progress: tuple[str, ...] = (),
) -> ResidentObjective:
    return ResidentObjective(
        id=objective_id,
        title=title,
        purpose=f"Advance {title}",
        serves_mandate_because="It advances the resident mandate.",
        expected_outcome="A proof artifact exists.",
        proof_criteria=("A proof artifact exists.",),
        kind=kind,
        status=status,
        pending_question=pending_question,
        reasoning=reasoning,
        source_evidence=source_evidence,
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
async def test_wake_integration_reads_portfolio_state_before_deciding(tmp_path: Path) -> None:
    backend = TrackingBackend(tmp_path)
    memory = LocalWakefulPortfolioStewardMemory(tmp_path)
    await _write_portfolio(
        backend,
        _objective("ready", "Ready objective", kind=ResidentObjectiveKind.TOOL_BUILDING.value),
    )
    steward = FakeSteward()

    await WakefulPortfolioStewardRuntime(
        backend=backend,
        steward=steward,
        memory=memory,
        config=WakefulPortfolioStewardConfig(max_wake_passes=1),
    ).run(MANDATE)

    assert steward.calls
    assert "read_portfolio" in backend.events
    assert "list_objectives" in backend.events


@pytest.mark.asyncio
async def test_attention_reason_is_derived_from_validation_state(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    memory = LocalWakefulPortfolioStewardMemory(tmp_path)
    await _write_portfolio(
        backend,
        _objective(
            "done",
            "Done without proof",
            status=ResidentObjectiveStatus.COMPLETED.value,
        ),
    )
    run = await WakefulPortfolioStewardRuntime(
        backend=backend,
        steward=FakeSteward(),
        memory=memory,
        config=WakefulPortfolioStewardConfig(max_wake_passes=1),
    ).run(MANDATE)

    assert "portfolio validation issue needs stewardship" in run.records[0].attention_reason


@pytest.mark.asyncio
async def test_no_stewardship_runs_when_no_meaningful_portfolio_work(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    memory = LocalWakefulPortfolioStewardMemory(tmp_path)
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE))
    steward = FakeSteward()

    run = await WakefulPortfolioStewardRuntime(
        backend=backend,
        steward=steward,
        memory=memory,
        config=WakefulPortfolioStewardConfig(max_wake_passes=1),
    ).run(MANDATE)

    assert steward.calls == []
    assert run.final_action == WakefulPortfolioStewardActionKind.SLEEP
    assert run.records[0].action_taken == WakefulPortfolioStewardActionKind.SLEEP


@pytest.mark.asyncio
async def test_steward_runs_when_validation_issues_exist(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    memory = LocalWakefulPortfolioStewardMemory(tmp_path)
    await _write_portfolio(
        backend,
        _objective("done", "Done without proof", status=ResidentObjectiveStatus.COMPLETED.value),
    )
    steward = FakeSteward()

    run = await WakefulPortfolioStewardRuntime(
        backend=backend,
        steward=steward,
        memory=memory,
        config=WakefulPortfolioStewardConfig(max_wake_passes=1),
    ).run(MANDATE)

    assert steward.calls == [MANDATE]
    assert run.records[0].action_taken == WakefulPortfolioStewardActionKind.RUN_STEWARD


@pytest.mark.asyncio
async def test_steward_runs_when_eligible_objective_exists(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    memory = LocalWakefulPortfolioStewardMemory(tmp_path)
    await _write_portfolio(backend, _objective("ready", "Ready objective"))
    steward = FakeSteward()

    run = await WakefulPortfolioStewardRuntime(
        backend=backend,
        steward=steward,
        memory=memory,
        config=WakefulPortfolioStewardConfig(max_wake_passes=1),
    ).run(MANDATE)

    assert steward.calls == [MANDATE]
    assert run.records[0].selected_objective is not None


@pytest.mark.asyncio
async def test_operator_needed_portfolio_state_routes_to_operator(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    memory = LocalWakefulPortfolioStewardMemory(tmp_path)
    await _write_portfolio(
        backend,
        _objective(
            "ask",
            "Ask operator",
            status=ResidentObjectiveStatus.NEEDS_OPERATOR.value,
            kind=ResidentObjectiveKind.OPERATOR_QUESTION.value,
            pending_question="May I continue?",
        ),
    )
    steward = FakeSteward()

    run = await WakefulPortfolioStewardRuntime(
        backend=backend,
        steward=steward,
        memory=memory,
        config=WakefulPortfolioStewardConfig(max_wake_passes=1),
    ).run(MANDATE)

    assert steward.calls == []
    assert run.final_action == WakefulPortfolioStewardActionKind.ASK_OPERATOR
    assert run.records[0].operator_questions == ("Ask operator",)


@pytest.mark.asyncio
async def test_bounded_multi_wake_loop_stops_at_configured_bounds(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    memory = LocalWakefulPortfolioStewardMemory(tmp_path)
    await _write_portfolio(backend, _objective("ready", "Ready objective"))
    steward = FakeSteward(
        ResidentPortfolioStewardRun(
            mandate=MANDATE,
            passes=(),
            final_action=ResidentPortfolioStewardActionKind.REPAIR,
            final_suggested_next_action="more stewardship remains",
            budget=ResidentBudgetSnapshot(
                turns_used=1,
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            ),
        )
    )

    run = await WakefulPortfolioStewardRuntime(
        backend=backend,
        steward=steward,
        memory=memory,
        config=WakefulPortfolioStewardConfig(max_wake_passes=2),
    ).run(MANDATE)

    assert len(run.records) == 2
    assert len(steward.calls) == 2
    assert run.final_action == WakefulPortfolioStewardActionKind.STOP
    assert "pass limit" in run.final_reason


@pytest.mark.asyncio
async def test_integration_records_are_persisted_with_steward_summary(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    memory = LocalWakefulPortfolioStewardMemory(tmp_path)
    await _write_portfolio(backend, _objective("ready", "Ready objective"))

    await WakefulPortfolioStewardRuntime(
        backend=backend,
        steward=ResidentPortfolioStewardRuntime(
            backend=backend,
            wake_runtime=_ProofWakeRuntime(),
            config=ResidentPortfolioStewardConfig(max_passes=1),
        ),
        memory=memory,
        config=WakefulPortfolioStewardConfig(max_wake_passes=1),
    ).run(MANDATE)
    records = await memory.list_records(MANDATE)

    assert records
    assert records[0].steward_summary
    assert records[0].persisted_refs


class _ProofWakeRuntime:
    async def run(self, mandate: str) -> object:
        from ravn.domain.wakeful_resident import (
            WakefulResidentCycleRecord,
            WakefulResidentDecisionKind,
            WakefulResidentRun,
        )

        cycle = WakefulResidentCycleRecord(
            cycle_number=1,
            mandate=mandate,
            prior_domain_model_ref="resident/domain-expert/domain-model.md",
            attention_reason="selected by wakeful portfolio steward integration",
            selected_action="advance selected portfolio objective",
            work_created_or_advanced=("portfolio objective advanced",),
            artifact_refs=("resident/domain-expert/artifacts/wakeful-portfolio-proof.md",),
            finding_summaries=("missing capability: generic follow-up review",),
            decision=WakefulResidentDecisionKind.STOP,
            decision_reason="max turns reached: 1",
            budget=ResidentBudgetSnapshot(
                turns_used=1,
                usage=TokenUsage(input_tokens=3, output_tokens=5),
            ),
        )
        return WakefulResidentRun(
            mandate=mandate,
            cycles=(cycle,),
            final_decision=WakefulResidentDecisionKind.STOP,
            final_reason=cycle.decision_reason,
            budget=cycle.budget,
        )


def test_portfolio_steward_attention_helper_mentions_missing_portfolio(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    # The runtime path tests this too; this pins the generic attention wording.
    import asyncio

    report = asyncio.run(ResidentPortfolioValidator(backend=backend).validate(MANDATE))

    assert (
        derive_portfolio_steward_attention_reason(mandate=MANDATE, validation=report)
        == "no resident portfolio exists; initialize stewardship from the mandate"
    )


def test_wakeful_portfolio_steward_contains_no_domain_specific_playbook_terms() -> None:
    source = Path("src/ravn/wakeful_resident.py").read_text(encoding="utf-8").casefold()

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
