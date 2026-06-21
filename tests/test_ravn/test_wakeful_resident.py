from __future__ import annotations

from pathlib import Path

import pytest

from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import ResidentBudgetSnapshot, ResidentTurnRecord
from ravn.domain.resident_expert import (
    ExpertArtifact,
    ExpertLoopDecision,
    ExpertLoopDecisionKind,
    ResidentDomainExpertRun,
    ResidentDomainModel,
    ResidentWorkstream,
    ResidentWorkstreamStatus,
    WorkstreamExecutionResult,
)
from ravn.domain.wakeful_resident import WakefulResidentCycleRecord, WakefulResidentDecisionKind
from ravn.resident_continuation import LocalResidentMemory
from ravn.resident_expert import LocalResidentDomainExpertMemory
from ravn.wakeful_resident import (
    LocalWakefulResidentMemory,
    WakefulResidentConfig,
    WakefulResidentRuntime,
    derive_attention_reason,
    derive_runtime_duplication_audit,
)

MANDATE = (
    "Kanuck Valley Models is my small 3D printing company. "
    "You are its resident Ravn. "
    "Help it become easier to run, more creative, and more successful. "
    "Ask before spending money or operating physical machines."
)


class RecordingExpertMemory(LocalResidentDomainExpertMemory):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.reads: list[str] = []
        self.workstream_lists: list[str] = []

    async def read_domain_model(self, mandate: str) -> ResidentDomainModel | None:
        self.reads.append(mandate)
        return await super().read_domain_model(mandate)

    async def list_workstreams(self, domain_model_ref: str) -> list[ResidentWorkstream]:
        self.workstream_lists.append(domain_model_ref)
        return await super().list_workstreams(domain_model_ref)


class StatefulExpertLoop:
    def __init__(
        self,
        memory: RecordingExpertMemory,
        runs: list[ResidentDomainExpertRun],
    ) -> None:
        self.memory = memory
        self.runs = runs
        self.calls: list[str] = []

    async def run(self, mandate: str) -> ResidentDomainExpertRun:
        self.calls.append(mandate)
        run = self.runs.pop(0)
        await self.memory.write_domain_model(run.domain_model)
        for workstream in run.workstreams:
            await self.memory.write_workstream(workstream)
        for artifact in run.artifacts:
            await self.memory.write_artifact(artifact, artifact.summary or artifact.title)
        return run


def _budget(tokens: int = 0, turns: int = 1) -> ResidentBudgetSnapshot:
    return ResidentBudgetSnapshot(
        turns_used=turns,
        usage=TokenUsage(input_tokens=tokens, output_tokens=0),
    )


def _workstream(
    workstream_id: str,
    title: str,
    *,
    status: str = ResidentWorkstreamStatus.COMPLETED.value,
) -> ResidentWorkstream:
    return ResidentWorkstream(
        id=workstream_id,
        title=title,
        purpose=f"Advance {title}",
        serves=title,
        expected_artifact="domain_brief",
        status=status,
        parent_domain_model_ref="resident/domain-expert/domain-model.md",
    )


def _run(
    *,
    model: ResidentDomainModel,
    workstreams: tuple[ResidentWorkstream, ...],
    decision: ExpertLoopDecisionKind = ExpertLoopDecisionKind.STOP,
    execution_results: tuple[WorkstreamExecutionResult, ...] = (),
    artifacts: tuple[ExpertArtifact, ...] = (),
    reason: str = "max turns reached: 1",
) -> ResidentDomainExpertRun:
    decision_workstream = workstreams[-1] if workstreams else None
    return ResidentDomainExpertRun(
        mandate=MANDATE,
        domain_model_ref="resident/domain-expert/domain-model.md",
        domain_model=model.with_workstreams(workstreams),
        workstreams=workstreams,
        artifacts=artifacts,
        execution_results=execution_results,
        decisions=(
            ExpertLoopDecision(
                kind=decision,
                reason=reason,
                workstream=decision_workstream,
            ),
        ),
        budget=_budget(tokens=7),
    )


def _model(
    *,
    opportunities: tuple[str, ...] = ("Map the next useful domain work",),
    open_questions: tuple[str, ...] = (),
    capability_gaps: tuple[str, ...] = (),
    artifacts: tuple[ExpertArtifact, ...] = (),
) -> ResidentDomainModel:
    return ResidentDomainModel(
        mandate=MANDATE,
        current_understanding="Resident understands the domain compactly.",
        hypotheses=("A useful domain concern exists.",),
        open_questions=open_questions,
        opportunities=opportunities,
        capability_gaps=capability_gaps,
        artifacts=artifacts,
    )


def _artifact(path: str = "resident/domain-expert/artifacts/brief.md") -> ExpertArtifact:
    return ExpertArtifact(
        title="Domain brief",
        kind="domain_brief",
        path=path,
        purpose="Capture resident work.",
        summary="A durable resident artifact.",
    )


def _cycle_record_without_runtime_audit() -> WakefulResidentCycleRecord:
    return WakefulResidentCycleRecord(
        cycle_number=1,
        mandate=MANDATE,
        prior_domain_model_ref="",
        attention_reason="older record",
        selected_action="none",
        work_created_or_advanced=(),
        artifact_refs=(),
        finding_summaries=(),
        decision=WakefulResidentDecisionKind.STOP,
        decision_reason="older record had no runtime audit",
        budget=_budget(),
    )


def _result(
    workstream_id: str,
    summary: str = "Advanced useful resident work.",
) -> WorkstreamExecutionResult:
    return WorkstreamExecutionResult(
        workstream_id=workstream_id,
        status=ResidentWorkstreamStatus.COMPLETED.value,
        summary=summary,
        facts=("learned one compact fact",),
        usage=TokenUsage(input_tokens=3, output_tokens=2),
    )


@pytest.mark.asyncio
async def test_wake_cycles_read_prior_persisted_state(tmp_path: Path) -> None:
    expert_memory = RecordingExpertMemory(tmp_path)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    ws1 = _workstream("first", "Create first brief")
    ws2 = _workstream("second", "Create second brief")
    loop = StatefulExpertLoop(
        expert_memory,
        [
            _run(
                model=_model(opportunities=("Create first brief",)),
                workstreams=(ws1,),
                execution_results=(_result("first"),),
                artifacts=(_artifact("resident/domain-expert/artifacts/first.md"),),
            ),
            _run(
                model=_model(opportunities=("Create second brief",)),
                workstreams=(ws1, ws2),
                execution_results=(_result("second"),),
                artifacts=(
                    _artifact("resident/domain-expert/artifacts/first.md"),
                    _artifact("resident/domain-expert/artifacts/second.md"),
                ),
            ),
        ],
    )
    runtime = WakefulResidentRuntime(
        expert_loop=loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=WakefulResidentConfig(max_wake_cycles=2),
    )

    run = await runtime.run(MANDATE)

    assert len(run.cycles) == 2
    assert len(expert_memory.reads) == 2
    assert run.cycles[0].prior_domain_model_ref == ""
    assert run.cycles[1].prior_domain_model_ref == "resident/domain-expert/domain-model.md"
    assert "domain opportunity" in run.cycles[1].attention_reason


def test_attention_reasons_are_derived_from_resident_state() -> None:
    model = _model(
        opportunities=(),
        open_questions=("Which source should anchor the next pass?",),
        capability_gaps=("Remote execution unavailable.",),
    )

    reason = derive_attention_reason(
        mandate=MANDATE,
        domain_model=model,
        workstreams=(),
    )

    assert (
        reason == "open domain question needs attention: Which source should anchor the next pass?"
    )


@pytest.mark.asyncio
async def test_first_cycle_can_orient_from_mandate_only(tmp_path: Path) -> None:
    expert_memory = RecordingExpertMemory(tmp_path)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    ws = _workstream("orientation", "Create orientation brief")
    loop = StatefulExpertLoop(
        expert_memory,
        [
            _run(
                model=_model(opportunities=("Create orientation brief",)),
                workstreams=(ws,),
                execution_results=(_result("orientation"),),
                artifacts=(_artifact(),),
            )
        ],
    )
    runtime = WakefulResidentRuntime(
        expert_loop=loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=WakefulResidentConfig(max_wake_cycles=1),
    )

    run = await runtime.run(MANDATE)

    assert (
        run.cycles[0].attention_reason == "no domain model exists; orient from the resident mandate"
    )
    assert run.cycles[0].artifact_refs


@pytest.mark.asyncio
async def test_operator_answer_is_recorded_as_wake_attention_signal(tmp_path: Path) -> None:
    expert_memory = RecordingExpertMemory(tmp_path)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    operator_memory = LocalResidentMemory(tmp_path / "operator")
    await operator_memory.write_operator_answer("Use the Prusa MK4 and PLA.")
    ws = _workstream("resume", "Resume from operator answer")
    loop = StatefulExpertLoop(
        expert_memory,
        [
            _run(
                model=_model(opportunities=("Resume from operator answer",)),
                workstreams=(ws,),
                execution_results=(_result("resume"),),
                artifacts=(_artifact(),),
            )
        ],
    )
    runtime = WakefulResidentRuntime(
        expert_loop=loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        operator_memory=operator_memory,
        config=WakefulResidentConfig(max_wake_cycles=1),
    )

    run = await runtime.run(MANDATE)

    assert run.cycles[0].attention_reason == (
        "operator answer is available; resume resident work from: Use the Prusa MK4 and PLA."
    )
    assert "operator_answer_available: resident/continuation/operator-answers/latest.md" in (
        run.cycles[0].runtime_audit
    )
    assert loop.calls == [MANDATE]


@pytest.mark.asyncio
async def test_later_cycle_can_continue_existing_workstream(tmp_path: Path) -> None:
    expert_memory = RecordingExpertMemory(tmp_path)
    existing = _workstream(
        "existing",
        "Continue resident work",
        status=ResidentWorkstreamStatus.PROPOSED.value,
    )
    await expert_memory.write_domain_model(_model(opportunities=()))
    await expert_memory.write_workstream(existing)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    completed = existing.with_status(ResidentWorkstreamStatus.COMPLETED.value)
    loop = StatefulExpertLoop(
        expert_memory,
        [
            _run(
                model=_model(opportunities=()),
                workstreams=(completed,),
                execution_results=(_result("existing"),),
                artifacts=(_artifact(),),
            )
        ],
    )
    runtime = WakefulResidentRuntime(
        expert_loop=loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=WakefulResidentConfig(max_wake_cycles=1),
    )

    run = await runtime.run(MANDATE)

    assert "actionable resident workstream exists" in run.cycles[0].attention_reason
    assert "proposed -> completed" in run.cycles[0].work_created_or_advanced[0]


@pytest.mark.asyncio
async def test_later_cycle_can_create_new_workstream_when_state_indicates_useful_work(
    tmp_path: Path,
) -> None:
    expert_memory = RecordingExpertMemory(tmp_path)
    existing = _workstream("done", "Completed brief")
    await expert_memory.write_domain_model(_model(opportunities=("Evaluate a new capability",)))
    await expert_memory.write_workstream(existing)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    created = _workstream("new", "Evaluate a new capability")
    loop = StatefulExpertLoop(
        expert_memory,
        [
            _run(
                model=_model(opportunities=("Evaluate a new capability",)),
                workstreams=(existing, created),
                execution_results=(_result("new"),),
                artifacts=(_artifact(),),
            )
        ],
    )
    runtime = WakefulResidentRuntime(
        expert_loop=loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=WakefulResidentConfig(max_wake_cycles=1),
    )

    run = await runtime.run(MANDATE)

    assert "domain opportunity may deserve work" in run.cycles[0].attention_reason
    assert any("new: created" in item for item in run.cycles[0].work_created_or_advanced)


@pytest.mark.asyncio
async def test_cycle_records_are_persisted_with_decisions_and_budget(tmp_path: Path) -> None:
    expert_memory = RecordingExpertMemory(tmp_path)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    ws = _workstream("persist", "Persist wake record")
    loop = StatefulExpertLoop(
        expert_memory,
        [
            _run(
                model=_model(opportunities=("Persist wake record",)),
                workstreams=(ws,),
                execution_results=(_result("persist"),),
                artifacts=(_artifact(),),
            )
        ],
    )
    runtime = WakefulResidentRuntime(
        expert_loop=loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=WakefulResidentConfig(max_wake_cycles=1),
    )

    await runtime.run(MANDATE)
    records = await wake_memory.list_wake_records(MANDATE)

    assert records
    assert records[0].decision == WakefulResidentDecisionKind.STOP
    assert records[0].budget.turns_used == 1
    assert records[0].budget.total_tokens == 7
    assert "canonical_runtime: ravn.wakeful_resident.WakefulResidentRuntime" in (
        records[0].runtime_audit
    )
    assert "duplication_check: no prior wake records found" in records[0].runtime_audit


@pytest.mark.asyncio
async def test_later_cycle_audits_prior_canonical_runtime_records(tmp_path: Path) -> None:
    expert_memory = RecordingExpertMemory(tmp_path)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    first = _workstream("first-audit", "Persist first audit")
    second = _workstream("second-audit", "Persist second audit")
    loop = StatefulExpertLoop(
        expert_memory,
        [
            _run(
                model=_model(opportunities=("Persist first audit",)),
                workstreams=(first,),
                execution_results=(_result("first-audit"),),
                artifacts=(_artifact("resident/domain-expert/artifacts/first-audit.md"),),
            ),
            _run(
                model=_model(opportunities=("Persist second audit",)),
                workstreams=(first, second),
                execution_results=(_result("second-audit"),),
                artifacts=(
                    _artifact("resident/domain-expert/artifacts/first-audit.md"),
                    _artifact("resident/domain-expert/artifacts/second-audit.md"),
                ),
            ),
        ],
    )
    runtime = WakefulResidentRuntime(
        expert_loop=loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=WakefulResidentConfig(max_wake_cycles=2),
    )

    run = await runtime.run(MANDATE)
    records = await wake_memory.list_wake_records(MANDATE)

    assert len(run.cycles) == 2
    assert "prior_canonical_runtime_records: 1" in run.cycles[1].runtime_audit
    assert "prior_records_without_runtime_audit: 0" in run.cycles[1].runtime_audit
    assert any(
        item == "duplication_check: resident wake memory shows canonical runtime continuity"
        for item in records[0].runtime_audit
    )


def test_runtime_duplication_audit_flags_unaudited_prior_records() -> None:
    prior = _cycle_record_without_runtime_audit()

    audit = derive_runtime_duplication_audit(recent_wake_records=(prior,))

    assert "prior_records_without_runtime_audit: 1" in audit
    assert "duplication_check: prior wake records need canonical audit backfill" in audit


@pytest.mark.asyncio
async def test_policy_gated_work_routes_to_operator_without_running_expert_loop(
    tmp_path: Path,
) -> None:
    expert_memory = RecordingExpertMemory(tmp_path)
    await expert_memory.write_domain_model(_model(opportunities=()))
    await expert_memory.write_workstream(
        _workstream(
            "approval",
            "Needs approval",
            status=ResidentWorkstreamStatus.NEEDS_OPERATOR.value,
        )
    )
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    loop = StatefulExpertLoop(expert_memory, [])
    runtime = WakefulResidentRuntime(
        expert_loop=loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=WakefulResidentConfig(max_wake_cycles=3),
    )

    run = await runtime.run(MANDATE)

    assert loop.calls == []
    assert run.final_decision == WakefulResidentDecisionKind.ASK_OPERATOR
    assert run.cycles[0].decision == WakefulResidentDecisionKind.ASK_OPERATOR


@pytest.mark.asyncio
async def test_pending_operator_marker_sleeps_before_spending_expert_turn(
    tmp_path: Path,
) -> None:
    expert_memory = RecordingExpertMemory(tmp_path)
    await expert_memory.write_domain_model(_model(opportunities=()))
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    operator_memory = LocalResidentMemory(tmp_path)
    await operator_memory.write_operator_needed(
        question="Which printer profile should I use?",
        reason="Need operator judgment before touching a physical workflow.",
        turn=ResidentTurnRecord(
            turn_index=1,
            prompt=MANDATE,
            response="",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        ),
    )
    loop = StatefulExpertLoop(expert_memory, [])
    runtime = WakefulResidentRuntime(
        expert_loop=loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        operator_memory=operator_memory,
        config=WakefulResidentConfig(max_wake_cycles=3),
    )

    run = await runtime.run(MANDATE)
    records = await wake_memory.list_wake_records(MANDATE)

    assert loop.calls == []
    assert run.final_decision == WakefulResidentDecisionKind.SLEEP
    assert run.final_reason == "waiting_for_operator"
    assert run.budget.turns_used == 0
    assert run.budget.total_tokens == 0
    assert records[0].decision == WakefulResidentDecisionKind.SLEEP
    assert records[0].decision_reason == "waiting_for_operator"
    assert records[0].attention_reason == (
        "operator input is pending; sleep until reply for: Which printer profile should I use?"
    )
    assert "Which printer profile should I use?" in records[0].selected_action
    assert records[0].budget.turns_used == 0
    assert "pending_operator_question: resident/continuation/operator-needed/latest.md" in (
        records[0].runtime_audit
    )


@pytest.mark.asyncio
async def test_runtime_budget_stops_continuation_without_losing_state(tmp_path: Path) -> None:
    expert_memory = RecordingExpertMemory(tmp_path)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    ws = _workstream("budget", "Spend one wake cycle")
    loop = StatefulExpertLoop(
        expert_memory,
        [
            _run(
                model=_model(opportunities=("Spend one wake cycle",)),
                workstreams=(ws,),
                execution_results=(_result("budget"),),
                artifacts=(_artifact(),),
            ),
            _run(model=_model(), workstreams=()),
        ],
    )
    runtime = WakefulResidentRuntime(
        expert_loop=loop,
        expert_memory=expert_memory,
        wake_memory=wake_memory,
        config=WakefulResidentConfig(max_wake_cycles=1),
    )

    run = await runtime.run(MANDATE)

    assert run.final_decision == WakefulResidentDecisionKind.STOP
    assert run.final_reason == "max turns reached: 1"
    assert len(await wake_memory.list_wake_records(MANDATE)) == 1
    assert len(loop.calls) == 1


def test_wakeful_runtime_contains_no_domain_specific_playbook_terms() -> None:
    source = Path("src/ravn/wakeful_resident.py").read_text(encoding="utf-8").casefold()

    for forbidden in (
        "kanuck",
        "inventory",
        "3d printing",
        "printer",
        "slicing",
        "slicer",
        "blender",
        "product catalog",
    ):
        assert forbidden not in source
