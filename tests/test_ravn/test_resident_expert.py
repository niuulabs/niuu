from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ravn.domain.models import TokenUsage, ToolCall, TurnResult
from ravn.domain.resident_continuation import ResidentPolicyObservation, ResidentTurnRecord
from ravn.domain.resident_expert import (
    ExpertArtifact,
    ExpertLoopDecisionKind,
    ResidentDomainModel,
    ResidentWorkstream,
    ResidentWorkstreamStatus,
    WorkstreamExecutionResult,
)
from ravn.resident_expert import (
    LocalResidentDomainExpertMemory,
    ResidentDomainExpertConfig,
    ResidentDomainExpertLoop,
    UnavailableWorkstreamExecutor,
    WorkstreamExecutionPort,
    build_domain_model_from_continuation,
    choose_work_product_kind,
)

MANDATE = (
    "Kanuck Valley Models is my small 3D printing company. "
    "You are its resident Ravn. "
    "Help it become easier to run, more creative, and more successful. "
    "Ask before spending money or operating physical machines."
)


def _outcome(
    selected_next_action: str,
    *,
    summary: str = "Resident oriented from mandate.",
    self_authored_work: str = "Create durable expert artifact from discovered concern",
) -> str:
    return f"""\
---outcome---
verdict: oriented
orientation_summary: {summary}
domain_hypotheses: [recurring domain concern should be made explicit]
open_questions: [which real source should anchor the next pass]
self_authored_work: [{self_authored_work}]
capability_gaps: [remote execution unavailable unless configured]
selected_next_action: {selected_next_action}
rationale: safe local expert work
---end---
"""


class FakeBackendAgent:
    def __init__(self, responses: list[TurnResult]) -> None:
        self._responses = iter(responses)
        self.prompts: list[str] = []
        self.tools = []
        self.max_iterations = 99
        self.llm_adapter_name = "fake-backend"
        self.checkpoint_port = None
        self.task_id = "expert-test"
        self._tools = {}
        self._interrupt_reason = None
        self.supports_steering = False
        self.steering_mode = "none"
        self.session = object()

    async def run_turn(self, user_input: str) -> TurnResult:
        self.prompts.append(user_input)
        return next(self._responses)

    def interrupt(self, reason: Any) -> None:
        self._interrupt_reason = reason

    async def steer(self, content: str) -> bool:
        return False


class RecordingExecutor(WorkstreamExecutionPort):
    def __init__(self, result: WorkstreamExecutionResult | None = None) -> None:
        self.calls: list[tuple[ResidentWorkstream, ResidentDomainModel]] = []
        self.result = result

    async def advance(
        self,
        workstream: ResidentWorkstream,
        *,
        domain_model: ResidentDomainModel,
    ) -> WorkstreamExecutionResult:
        self.calls.append((workstream, domain_model))
        return self.result or WorkstreamExecutionResult(
            workstream_id=workstream.id,
            status=ResidentWorkstreamStatus.COMPLETED.value,
            summary="Produced a durable expert artifact.",
            artifact_refs=("artifacts/domain-brief.md",),
            facts=("artifact produced from workstream outcome",),
            lessons=("bounded workstream execution worked",),
            usage=TokenUsage(input_tokens=3, output_tokens=4),
        )


def _turn(text: str, *, tokens: int = 5) -> TurnResult:
    return TurnResult(
        response=text,
        tool_calls=[ToolCall(id="tc", name="mimir_write", input={})],
        tool_results=[],
        usage=TokenUsage(input_tokens=tokens, output_tokens=tokens),
    )


@pytest.mark.asyncio
async def test_domain_model_is_persisted_and_updated_from_outcomes(tmp_path: Path) -> None:
    agent = FakeBackendAgent([_turn(_outcome("Write a compact expert brief."))])
    memory = LocalResidentDomainExpertMemory(tmp_path)
    loop = ResidentDomainExpertLoop(
        agent=agent,
        expert_memory=memory,
        executor=RecordingExecutor(),
        config=ResidentDomainExpertConfig(),
    )

    run = await loop.run(MANDATE)

    assert (tmp_path / run.domain_model_ref).exists()
    assert run.domain_model.current_understanding == "Resident oriented from mandate."
    assert run.domain_model.hypotheses
    assert run.domain_model.open_questions
    assert run.domain_model.capability_gaps


@pytest.mark.asyncio
async def test_prior_domain_model_is_read_before_planning(tmp_path: Path) -> None:
    memory = LocalResidentDomainExpertMemory(tmp_path)
    prior = ResidentDomainModel(
        mandate=MANDATE,
        current_understanding="Prior understanding",
        opportunities=("Draft a capability evaluation for the most uncertain gap",),
    )
    await memory.write_domain_model(prior)
    agent = FakeBackendAgent([_turn(_outcome("Write a compact expert brief."))])
    executor = RecordingExecutor()
    loop = ResidentDomainExpertLoop(
        agent=agent,
        expert_memory=memory,
        executor=executor,
        config=ResidentDomainExpertConfig(),
    )

    run = await loop.run(MANDATE)

    assert run.workstreams
    assert "capability evaluation" in run.workstreams[0].title
    assert executor.calls[0][1].opportunities[0] == prior.opportunities[0]


@pytest.mark.asyncio
async def test_domain_model_readback_preserves_artifacts_and_policy_observations(
    tmp_path: Path,
) -> None:
    memory = LocalResidentDomainExpertMemory(tmp_path)
    model = ResidentDomainModel(
        mandate=MANDATE,
        current_understanding="Resident understanding",
        learned_policy_observations=(
            ResidentPolicyObservation(
                subject="question:approval-boundary",
                observation="Ask before touching physical machines.",
                source="operator_answer",
                status="candidate",
            ),
        ),
        artifacts=(
            ExpertArtifact(
                title="Capability brief",
                kind="capability_evaluation",
                path="resident/domain-expert/artifacts/capability-brief.md",
                purpose="Capture a durable evaluation.",
            ),
        ),
    )

    await memory.write_domain_model(model)
    restored = await memory.read_domain_model(MANDATE)

    assert restored is not None
    assert restored.learned_policy_observations
    assert restored.learned_policy_observations[0].subject == "question:approval-boundary"
    assert restored.artifacts
    assert restored.artifacts[0].path == "resident/domain-expert/artifacts/capability-brief.md"


def test_domain_model_recovers_colon_bearing_outcome_lists_from_raw_response() -> None:
    response = """\
---outcome---
orientation_summary: Resident oriented.
domain_hypotheses: ["Inferred: first concern", "Inferred: second concern"]
open_questions: ["Question: which source matters?", "Question: which gap is urgent?"]
capability_gaps: ["Gap: no source data yet"]
self_authored_work: ["Created: domain map"]
selected_next_action: "Write a compact brief."
rationale: safe local work
---end---
"""
    record = ResidentTurnRecord(
        turn_index=1,
        prompt=MANDATE,
        response=response,
        outcome_fields={
            "orientation_summary": "Resident oriented.",
            "domain_hypotheses": '["Inferred: first concern", "',
            "open_questions": '["Question: which source matters?", "',
            "capability_gaps": '["Gap: no source data yet"]',
            "self_authored_work": '["Created: domain map"]',
            "selected_next_action": "Write a compact brief.",
            "rationale": "safe local work",
        },
        tool_names=("WebSearch",),
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )

    model = build_domain_model_from_continuation(
        MANDATE,
        prior_model=None,
        turns=(record,),
    )

    assert model.hypotheses == ("Inferred: first concern", "Inferred: second concern")
    assert model.open_questions == (
        "Question: which source matters?",
        "Question: which gap is urgent?",
    )
    assert model.capability_gaps == ("Gap: no source data yet",)
    assert "Created: domain map" in model.opportunities


def test_work_product_selection_is_generic_not_domain_specific() -> None:
    assert choose_work_product_kind("research competing options").value == "research_brief"
    assert choose_work_product_kind("write product requirements").value == "prd"
    assert choose_work_product_kind("define system requirements").value == "srd"
    assert choose_work_product_kind("build an operating ledger").value == "implementation_plan"


@pytest.mark.asyncio
async def test_workstreams_are_persisted_with_status_budget_and_risk_metadata(
    tmp_path: Path,
) -> None:
    agent = FakeBackendAgent([_turn(_outcome("Build a compact operating process."))])
    memory = LocalResidentDomainExpertMemory(tmp_path)
    loop = ResidentDomainExpertLoop(
        agent=agent,
        expert_memory=memory,
        executor=RecordingExecutor(),
        config=ResidentDomainExpertConfig(),
    )

    run = await loop.run(MANDATE)

    assert run.workstreams
    workstream_path = (
        tmp_path / "resident/domain-expert/workstreams" / f"{run.workstreams[0].id}.md"
    )
    text = workstream_path.read_text(encoding="utf-8")
    assert "- status: completed" in text
    assert "- budget_estimate: small" in text
    assert "## Risk Boundaries" in text


@pytest.mark.asyncio
async def test_safe_workstream_advances_and_consolidates_into_domain_model(
    tmp_path: Path,
) -> None:
    agent = FakeBackendAgent([_turn(_outcome("Write an expert artifact."))])
    result = WorkstreamExecutionResult(
        workstream_id="placeholder",
        status=ResidentWorkstreamStatus.COMPLETED.value,
        summary="Learned that a compact brief is useful.",
        artifact_refs=("briefs/first.md",),
        facts=("brief should stay compact",),
        lessons=("review outcome before adding more work",),
        capability_gaps=("remote sessions not configured",),
        policy_observations=(
            ResidentPolicyObservation(
                subject="boundary:spending",
                observation="Spending still needs explicit approval.",
                source="workstream_review",
            ),
        ),
        usage=TokenUsage(input_tokens=11, output_tokens=7),
    )
    loop = ResidentDomainExpertLoop(
        agent=agent,
        expert_memory=LocalResidentDomainExpertMemory(tmp_path),
        executor=RecordingExecutor(result),
        config=ResidentDomainExpertConfig(),
    )

    run = await loop.run(MANDATE)

    assert run.execution_results
    assert "brief should stay compact" in run.domain_model.known_facts
    assert "remote sessions not configured" in run.domain_model.capability_gaps
    assert run.domain_model.learned_policy_observations
    assert run.artifacts
    assert (tmp_path / run.artifacts[-1].path).exists()


@pytest.mark.asyncio
async def test_unsafe_workstream_asks_or_pauses(tmp_path: Path) -> None:
    agent = FakeBackendAgent([_turn(_outcome("Operate a physical machine now."))])
    loop = ResidentDomainExpertLoop(
        agent=agent,
        expert_memory=LocalResidentDomainExpertMemory(tmp_path),
        executor=RecordingExecutor(),
        config=ResidentDomainExpertConfig(),
    )

    run = await loop.run(MANDATE)

    assert run.final_decision is not None
    assert run.final_decision.kind == ExpertLoopDecisionKind.ASK_OPERATOR
    assert run.execution_results == ()
    assert run.workstreams[0].status == ResidentWorkstreamStatus.NEEDS_OPERATOR.value


@pytest.mark.asyncio
async def test_execution_backend_is_behind_a_port(tmp_path: Path) -> None:
    agent = FakeBackendAgent([_turn(_outcome("Write a compact expert brief."))])
    executor = RecordingExecutor()
    loop = ResidentDomainExpertLoop(
        agent=agent,
        expert_memory=LocalResidentDomainExpertMemory(tmp_path),
        executor=executor,
        config=ResidentDomainExpertConfig(),
    )

    await loop.run(MANDATE)

    assert executor.calls
    assert isinstance(executor.calls[0][0], ResidentWorkstream)


@pytest.mark.asyncio
async def test_existing_proposed_workstream_can_be_advanced(tmp_path: Path) -> None:
    memory = LocalResidentDomainExpertMemory(tmp_path)
    prior = ResidentDomainModel(
        mandate=MANDATE,
        current_understanding="Prior understanding",
        opportunities=("Draft the next useful brief",),
    )
    await memory.write_domain_model(prior)
    existing = ResidentWorkstream(
        id="existing-brief",
        title="Draft the next useful brief",
        purpose="Advance resident domain expertise through domain brief.",
        serves="Draft the next useful brief",
        expected_artifact="domain_brief",
        parent_domain_model_ref="resident/domain-expert/domain-model.md",
        status=ResidentWorkstreamStatus.PROPOSED.value,
    )
    await memory.write_workstream(existing)
    agent = FakeBackendAgent([_turn(_outcome("Draft the next useful brief"))])
    executor = RecordingExecutor()
    loop = ResidentDomainExpertLoop(
        agent=agent,
        expert_memory=memory,
        executor=executor,
        config=ResidentDomainExpertConfig(),
    )

    run = await loop.run(MANDATE)

    assert executor.calls
    assert executor.calls[0][0].id == "existing-brief"
    assert run.workstreams[0].status == ResidentWorkstreamStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_unavailable_remote_execution_records_capability_gap(tmp_path: Path) -> None:
    agent = FakeBackendAgent([_turn(_outcome("Build using a remote execution session."))])
    loop = ResidentDomainExpertLoop(
        agent=agent,
        expert_memory=LocalResidentDomainExpertMemory(tmp_path),
        executor=UnavailableWorkstreamExecutor("forge session"),
        config=ResidentDomainExpertConfig(),
    )

    run = await loop.run(MANDATE)

    assert run.execution_results[0].status == ResidentWorkstreamStatus.PAUSED.value
    assert "forge session unavailable for workstream execution" in run.domain_model.capability_gaps


def test_expert_loop_contains_no_domain_specific_playbook_terms() -> None:
    source = Path("src/ravn/resident_expert.py").read_text(encoding="utf-8").casefold()

    for forbidden in ("kanuck", "inventory", "3d printing", "slicer", "stl"):
        assert forbidden not in source
