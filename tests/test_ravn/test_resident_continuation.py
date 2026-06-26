from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ravn.domain.models import TokenUsage, ToolCall, TurnResult
from ravn.domain.operator_contact import (
    BroadcastThenCallbackOperatorContact,
    CallbackOperatorContact,
    ChannelOperatorContact,
    OperatorContactRequest,
    OperatorContactResult,
    OperatorContactStatus,
)
from ravn.domain.resident_continuation import (
    ContinuationDecisionKind,
    ResidentBudgetLimits,
    ResidentMemoryEntry,
    ResidentPolicyDecision,
    ResidentPolicyDecisionRecord,
    ResidentPolicyObservation,
    ResidentTurnRecord,
)
from ravn.resident_continuation import (
    ConfigurableResidentPolicy,
    LocalResidentMemory,
    ResidentContinuationKernel,
    ResidentPolicyBoundary,
    ResidentRunBudget,
)
from ravn.resident_operator_contact import ResidentOperatorContactCoordinator
from ravn.adapters.resident_state.mimir import MimirResidentState

MANDATE = (
    "Kanuck Valley Models is my small 3D printing company. "
    "You are its resident Ravn. "
    "Help it become easier to run, more creative, and more successful. "
    "Ask before spending money or operating physical machines."
)


def _outcome(selected_next_action: str, *, rationale: str = "safe local work") -> str:
    return f"""\
---outcome---
verdict: oriented
orientation_summary: Oriented from mandate and discovered context.
domain_hypotheses: [recurring domain concerns should be mapped from evidence]
open_questions: []
self_authored_work: [continue evidence-backed discovery]
capability_gaps: []
selected_next_action: {selected_next_action}
rationale: {rationale}
---end---
"""


def _help_needed_outcome(question: str) -> str:
    return f"""\
---outcome---
verdict: help_needed
orientation_summary: The resident needs operator facts before continuing.
domain_hypotheses: []
open_questions: [{question}]
self_authored_work: []
capability_gaps: []
selected_next_action: Wait for operator input.
rationale: continuing would require guessing
reason: Need operator-provided facts.
recommendation: {question}
attempted: [prepared safe local scaffolding]
---end---
"""


def _blocked_operator_outcome() -> str:
    return """\
---outcome---
verdict: blocked
orientation_summary: The selected work is blocked on operator input.
domain_hypotheses: []
open_questions: []
self_authored_work: []
capability_gaps: []
selected_next_action: Wait for operator-provided setup facts.
rationale: importing now would fabricate records
reason: Blocked on real facts from the operator.
recommendation: Provide the missing facts.
---end---
"""


class FakeBackendAgent:
    """Executor-shaped fake proving the kernel does not depend on RavnAgent."""

    def __init__(self, responses: list[TurnResult]) -> None:
        self._responses = iter(responses)
        self.prompts: list[str] = []
        self.tools = [_NamedTool("mimir_write"), _NamedTool("glob_search")]
        self.max_iterations = 99
        self.llm_adapter_name = "fake-backend"
        self.checkpoint_port = None
        self.task_id = "test"
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


class _NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


class RecordingMemory:
    def __init__(self, recall_entries: list[ResidentMemoryEntry] | None = None) -> None:
        self.recall_entries = recall_entries or []
        self.recall_calls: list[str] = []
        self.turns: list[ResidentTurnRecord] = []
        self.budgets: list[Any] = []
        self.policy_observations: list[ResidentPolicyObservation] = []
        self.policy_decisions: list[ResidentPolicyDecisionRecord] = []
        self.pending_question: str = ""
        self.pending_reason: str = ""
        self.answer: str = ""
        self.consumed_answers: list[str] = []

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        self.recall_calls.append(mandate)
        return self.recall_entries[:limit]

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        self.turns.append(record)
        return f"turn-{record.turn_index}"

    async def write_budget(self, snapshot: Any) -> str:
        self.budgets.append(snapshot)
        return "budget"

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str:
        self.policy_observations.append(observation)
        return "policy"

    async def list_policy_observations(self) -> list[ResidentPolicyObservation]:
        return list(self.policy_observations)

    async def write_policy_decision(self, decision: ResidentPolicyDecisionRecord) -> str:
        self.policy_decisions.append(decision)
        return "policy-decision"

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
    ) -> str:
        self.pending_question = question
        self.pending_reason = reason
        return "resident/continuation/operator-needed/latest.md"

    async def read_operator_needed(self) -> ResidentMemoryEntry | None:
        if not self.pending_question:
            return None
        return ResidentMemoryEntry(
            path="resident/continuation/operator-needed/latest.md",
            summary="Operator Input Needed",
            content=(
                "# Operator Input Needed\n\n"
                f"- status: pending\n- question: {self.pending_question}\n"
            ),
        )

    async def write_operator_answer(self, answer: str) -> str:
        self.answer = answer
        self.pending_question = ""
        return "resident/continuation/operator-answers/latest.md"

    async def read_operator_answer(self) -> ResidentMemoryEntry | None:
        if not self.answer:
            return None
        return ResidentMemoryEntry(
            path="resident/continuation/operator-answers/latest.md",
            summary="Operator Answer",
            content=f"# Operator Answer\n\n{self.answer}\n",
        )

    async def consume_operator_answer(self, answer: ResidentMemoryEntry) -> str:
        self.consumed_answers.append(answer.path)
        self.answer = ""
        return answer.path


class RecordingPolicy:
    def __init__(self, decision: ResidentPolicyDecision) -> None:
        self.decision = decision
        self.contexts: list[Any] = []

    async def assess(self, action: Any, *, context: Any) -> ResidentPolicyDecision:
        self.contexts.append(context)
        return self.decision


class RecordingOperatorContact:
    def __init__(self, result_answer: str = "") -> None:
        self.result_answer = result_answer
        self.requests: list[OperatorContactRequest] = []

    async def ask(self, request: OperatorContactRequest) -> OperatorContactResult:
        self.requests.append(request)
        if self.result_answer:
            return OperatorContactResult(
                request=request,
                status=OperatorContactStatus.ANSWERED.value,
                answer=self.result_answer,
            )
        return OperatorContactResult(
            request=request,
            status=OperatorContactStatus.PENDING.value,
        )


class RecordingChannel:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_channel_operator_contact_emits_existing_help_needed_event() -> None:
    channel = RecordingChannel()
    contact = ChannelOperatorContact(
        channel=channel,
        source="resident-proof",
        persona="domain-drive",
        session_id="session-1",
    )
    request = OperatorContactRequest(
        id="approval-1",
        question="May I operate the printer?",
        reason="physical machines require approval",
        impact="Resident will wait instead of operating the machine.",
        source_objective_id="objective-1",
        risk_boundaries=("physical_operation",),
    )

    result = await contact.ask(request)

    assert result.status == OperatorContactStatus.PENDING.value
    assert len(channel.events) == 1
    event = channel.events[0]
    assert event.type.value == "help_needed"
    assert event.correlation_id == "approval-1"
    assert event.session_id == "session-1"
    assert event.payload["summary"] == "May I operate the printer?"
    assert event.payload["context"]["source_objective_id"] == "objective-1"
    assert event.payload["context"]["risk_boundaries"] == ["physical_operation"]


@pytest.mark.asyncio
async def test_broadcast_then_callback_operator_contact_keeps_audit_and_answer() -> None:
    channel = RecordingChannel()
    broadcast = ChannelOperatorContact(
        channel=channel,
        source="resident-proof",
        persona="domain-drive",
    )

    async def answer(question: str) -> str:
        assert question == "Which printer should I use?"
        return "Use the Prusa MK4."

    contact = BroadcastThenCallbackOperatorContact(
        broadcast=broadcast,
        callback=CallbackOperatorContact(ask_operator=answer),
    )

    result = await contact.ask(
        OperatorContactRequest(
            id="clarify-1",
            question="Which printer should I use?",
            reason="needs operator facts",
            impact="Resident cannot safely continue without an answer.",
        )
    )

    assert len(channel.events) == 1
    assert result.status == OperatorContactStatus.ANSWERED.value
    assert result.answer == "Use the Prusa MK4."


def _turn(text: str, *, tool_name: str = "mimir_write", tokens: int = 10) -> TurnResult:
    return TurnResult(
        response=text,
        tool_calls=[ToolCall(id=f"tc-{tool_name}", name=tool_name, input={})],
        tool_results=[],
        usage=TokenUsage(input_tokens=tokens, output_tokens=tokens),
    )


@pytest.mark.asyncio
async def test_operator_contact_coordinator_suppresses_existing_pending_question() -> None:
    memory = RecordingMemory()
    memory.pending_question = "Which printer should I use?"
    contact = RecordingOperatorContact()
    turn = ResidentTurnRecord(
        turn_index=1,
        prompt=MANDATE,
        response="needs help",
        outcome_fields={},
        tool_names=(),
        usage=TokenUsage(input_tokens=0, output_tokens=0),
    )
    coordinator = ResidentOperatorContactCoordinator(memory=memory, contact=contact)

    report = await coordinator.contact_operator(
        OperatorContactRequest(
            question="Which printer should I use?",
            reason="needs_context",
            impact="Resident cannot safely continue without an answer.",
        ),
        turn=turn,
    )

    assert report.result.status == OperatorContactStatus.SUPPRESSED.value
    assert report.suppressed_existing_pending is not None
    assert contact.requests == []
    assert memory.pending_question == "Which printer should I use?"


@pytest.mark.asyncio
async def test_continuation_operator_contact_uses_purpose_and_persists_feedback() -> None:
    agent = FakeBackendAgent(
        [
            _turn(_outcome("Start the printer to validate the selected terrain model.")),
        ]
    )
    memory = RecordingMemory()
    policy = RecordingPolicy(
        ResidentPolicyDecision(
            allowed=False,
            needs_approval=True,
            reason="physical machines require approval",
            question="May I start the printer for this validation?",
            risk_boundaries=("physical_operation",),
        )
    )
    channel = RecordingChannel()

    async def approve(question: str) -> str:
        assert question == "May I start the printer for this validation?"
        return "Approved for this proof.\npolicy: accepted preference:ask:physical"

    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        policy=policy,
        channel=channel,
        ask_operator=approve,
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=2)),
    )

    run = await kernel.run(MANDATE)

    assert run.final_decision is not None
    assert run.final_decision.kind == ContinuationDecisionKind.ASK_OPERATOR
    assert memory.pending_reason.startswith("approval:")
    assert memory.answer.startswith("Approved for this proof.")
    assert len(channel.events) == 1
    event = channel.events[0]
    assert event.payload["context"]["operator_contact_purpose"] == "approval"
    assert event.payload["context"]["risk_boundaries"] == ["physical_operation"]
    assert any(
        item.subject == "operator-contact:approval" for item in memory.policy_observations
    )
    assert any(
        item.subject == "preference:ask:physical" and item.status == "accepted"
        for item in memory.policy_observations
    )


@pytest.mark.asyncio
async def test_continuation_kernel_is_backend_agnostic_and_continues_safe_action() -> None:
    agent = FakeBackendAgent(
        [
            _turn(_outcome("Write a compact local domain map from discovered notes.")),
            _turn(_outcome("Sleep until new context appears.")),
        ]
    )
    memory = RecordingMemory()
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=2)),
    )

    run = await kernel.run(MANDATE)

    assert len(run.turns) == 2
    assert agent.prompts[0] == MANDATE
    assert "resident continuing its own selected safe next action" in agent.prompts[1]
    assert run.decisions[0].kind == ContinuationDecisionKind.CONTINUE
    assert run.final_decision is not None
    assert run.final_decision.kind == ContinuationDecisionKind.STOP
    assert "max turns reached" in run.final_decision.reason
    assert [record.turn_index for record in memory.turns] == [1, 2]


@pytest.mark.asyncio
async def test_unsafe_or_uncertain_action_asks_instead_of_executing() -> None:
    agent = FakeBackendAgent(
        [_turn(_outcome("Operate a physical machine to validate the next iteration."))]
    )
    memory = RecordingMemory()
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        policy=ConfigurableResidentPolicy(),
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=4)),
    )

    run = await kernel.run(MANDATE)

    assert len(run.turns) == 1
    assert run.final_decision is not None
    assert run.final_decision.kind == ContinuationDecisionKind.ASK_OPERATOR
    assert "physical_operation" in run.final_decision.reason
    assert len(agent.prompts) == 1


@pytest.mark.asyncio
async def test_safe_action_with_negated_risk_in_rationale_can_continue() -> None:
    agent = FakeBackendAgent(
        [
            _turn(
                _outcome(
                    "Build an initial operating binder in the workspace.",
                    rationale="It costs nothing and does not operate machines.",
                )
            ),
            _turn(_outcome("Sleep until new context appears.")),
        ]
    )
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=RecordingMemory(),
        policy=ConfigurableResidentPolicy(),
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=2)),
    )

    run = await kernel.run(MANDATE)

    assert len(run.turns) == 2
    assert run.decisions[0].kind == ContinuationDecisionKind.CONTINUE


@pytest.mark.asyncio
async def test_safe_action_with_negated_risk_in_title_can_continue() -> None:
    agent = FakeBackendAgent(
        [
            _turn(_outcome("Create a no-spend, non-machine operating template.")),
            _turn(_outcome("Sleep until new context appears.")),
        ]
    )
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=RecordingMemory(),
        policy=ConfigurableResidentPolicy(),
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=2)),
    )

    run = await kernel.run(MANDATE)

    assert len(run.turns) == 2
    assert run.decisions[0].kind == ContinuationDecisionKind.CONTINUE


@pytest.mark.asyncio
async def test_budget_exhaustion_stops_cleanly_after_turn() -> None:
    agent = FakeBackendAgent([_turn(_outcome("Write a compact local note."))])
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=RecordingMemory(),
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=1)),
    )

    run = await kernel.run(MANDATE)

    assert len(run.turns) == 1
    assert run.final_decision is not None
    assert run.final_decision.kind == ContinuationDecisionKind.STOP
    assert run.final_decision.reason == "max turns reached: 1"


@pytest.mark.asyncio
async def test_memory_is_written_and_recalled_before_decision() -> None:
    memory = RecordingMemory(
        [ResidentMemoryEntry(path="resident/continuation/turns/old.md", summary="prior map")]
    )
    policy = RecordingPolicy(
        ResidentPolicyDecision(
            allowed=True,
            needs_approval=False,
            reason="test policy allows",
        )
    )
    agent = FakeBackendAgent(
        [
            _turn(_outcome("Write a compact local domain map.")),
            _turn(_outcome("Sleep until a new signal arrives.")),
        ]
    )
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        policy=policy,
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=2)),
    )

    await kernel.run(MANDATE)

    assert memory.turns
    assert memory.budgets
    assert memory.recall_calls == [MANDATE, MANDATE]
    assert policy.contexts[0].recent_memory[0].summary == "prior map"


@pytest.mark.asyncio
async def test_operator_answer_becomes_persisted_policy_observation() -> None:
    async def answer(question: str) -> str:
        return (
            "For this environment, ask every time before operating hardware.\n"
            "policy: accept soft-ask:physical_operation because hardware stays gated."
        )

    agent = FakeBackendAgent([_turn(_outcome("Start a physical device for validation."))])
    memory = RecordingMemory()
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        policy=ConfigurableResidentPolicy(),
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=3)),
        ask_operator=answer,
    )

    run = await kernel.run(MANDATE)

    assert run.final_decision is not None
    assert run.final_decision.kind == ContinuationDecisionKind.ASK_OPERATOR
    assert len(memory.policy_observations) == 2
    assert memory.policy_observations[0].source == "operator_answer"
    assert "operating hardware" in memory.policy_observations[0].observation
    assert memory.policy_observations[1].subject == "soft-ask:physical_operation"
    assert memory.policy_observations[1].status == "accepted"


@pytest.mark.asyncio
async def test_help_needed_writes_pending_marker_and_stops_without_looping() -> None:
    agent = FakeBackendAgent([_turn(_help_needed_outcome("What printer should I use?"))])
    memory = RecordingMemory()
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=5)),
    )

    run = await kernel.run(MANDATE)

    assert len(run.turns) == 1
    assert len(agent.prompts) == 1
    assert run.final_decision is not None
    assert run.final_decision.kind == ContinuationDecisionKind.ASK_OPERATOR
    assert "operator-provided facts" in run.final_decision.reason
    assert memory.pending_question == "What printer should I use?"


@pytest.mark.asyncio
async def test_pending_operator_question_sleeps_before_spending_turn() -> None:
    agent = FakeBackendAgent([_turn(_outcome("This should not run."))])
    memory = RecordingMemory()
    await memory.write_operator_needed(
        question="What printer should I use?",
        reason="Need operator facts.",
        turn=ResidentTurnRecord(
            turn_index=1,
            prompt=MANDATE,
            response="",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        ),
    )
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=5)),
    )

    run = await kernel.run(MANDATE)

    assert run.turns == ()
    assert agent.prompts == []
    assert run.final_decision is not None
    assert run.final_decision.kind == ContinuationDecisionKind.SLEEP
    assert run.final_decision.reason == "waiting_for_operator"


@pytest.mark.asyncio
async def test_operator_answer_is_injected_into_resume_prompt() -> None:
    agent = FakeBackendAgent([_turn(_outcome("Apply the operator-provided setup facts."))])
    memory = RecordingMemory()
    await memory.write_operator_answer("Use the Prusa MK4 and PLA.")
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=1)),
    )

    run = await kernel.run(MANDATE)

    assert "Operator answer memory" in agent.prompts[0]
    assert "Use the Prusa MK4 and PLA." in agent.prompts[0]
    assert run.policy_observations
    assert run.policy_observations[0].source == "operator_answer"
    assert "Use the Prusa MK4 and PLA." in run.policy_observations[0].observation
    assert memory.policy_observations
    assert memory.consumed_answers == ["resident/continuation/operator-answers/latest.md"]


@pytest.mark.asyncio
async def test_consumed_operator_answer_is_not_reused_on_next_wake() -> None:
    first_agent = FakeBackendAgent([_turn(_outcome("Apply the operator-provided setup facts."))])
    memory = RecordingMemory()
    await memory.write_operator_answer("Use the Prusa MK4 and PLA.")
    first_kernel = ResidentContinuationKernel(
        agent=first_agent,
        memory=memory,
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=1)),
    )

    await first_kernel.run(MANDATE)

    second_agent = FakeBackendAgent([_turn(_outcome("Inspect current resident memory."))])
    second_kernel = ResidentContinuationKernel(
        agent=second_agent,
        memory=memory,
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=1)),
    )

    await second_kernel.run(MANDATE)

    assert "Operator answer memory" in first_agent.prompts[0]
    assert "Operator answer memory" not in second_agent.prompts[0]


@pytest.mark.asyncio
async def test_preseeded_operator_answer_policy_line_is_preserved() -> None:
    agent = FakeBackendAgent([_turn(_outcome("Research candidate tools using local notes."))])
    memory = RecordingMemory()
    await memory.write_operator_answer(
        "Safe research may continue.\n"
        "policy: accept soft-allow:research because Safe research is low risk."
    )
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        policy=ConfigurableResidentPolicy(
            soft_boundaries=(
                ResidentPolicyBoundary(name="research", terms=("research",), hard=False),
            )
        ),
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=1)),
    )

    run = await kernel.run(MANDATE)

    assert any(
        observation.subject == "soft-allow:research"
        and observation.status == "accepted"
        for observation in run.policy_observations
    )


@pytest.mark.asyncio
async def test_blocked_operator_outcome_writes_pending_marker() -> None:
    agent = FakeBackendAgent([_turn(_blocked_operator_outcome())])
    memory = RecordingMemory()
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=5)),
    )

    run = await kernel.run(MANDATE)

    assert run.final_decision is not None
    assert run.final_decision.kind == ContinuationDecisionKind.ASK_OPERATOR
    assert memory.pending_question == "Provide the missing facts."


@pytest.mark.asyncio
async def test_local_memory_persists_turn_budget_and_policy_observation(tmp_path: Path) -> None:
    memory = LocalResidentMemory(tmp_path)
    record = ResidentTurnRecord(
        turn_index=1,
        prompt="mandate",
        response="response",
        outcome_fields={"selected_next_action": "write note"},
        tool_names=("mimir_write",),
        usage=TokenUsage(input_tokens=1, output_tokens=2),
    )

    turn_ref = await memory.write_turn(record)
    budget_ref = await memory.write_budget(
        ResidentRunBudget(ResidentBudgetLimits(max_turns=1)).snapshot()
    )
    policy_ref = await memory.write_policy_observation(
        ResidentPolicyObservation(
            subject="boundary:physical_operation",
            observation="ask first",
            source="operator_answer",
        )
    )

    assert (tmp_path / turn_ref).exists()
    assert (tmp_path / budget_ref).exists()
    assert (tmp_path / policy_ref).exists()
    recalled = await memory.recall(MANDATE)
    assert any(entry.path == turn_ref for entry in recalled)
    listed = await memory.list_policy_observations()
    assert listed[0].subject == "boundary:physical_operation"


@pytest.mark.asyncio
async def test_local_memory_consumes_operator_answer_without_losing_audit(tmp_path: Path) -> None:
    memory = LocalResidentMemory(tmp_path)
    await memory.write_operator_answer("Use the Prusa MK4 and PLA.")

    answer = await memory.read_operator_answer()
    assert answer is not None
    assert "Use the Prusa MK4 and PLA." in answer.content

    consumed_ref = await memory.consume_operator_answer(answer)

    assert consumed_ref == "resident/continuation/operator-answers/latest.md"
    assert await memory.read_operator_answer() is None
    latest = tmp_path / "resident/continuation/operator-answers/latest.md"
    assert "- status: consumed" in latest.read_text(encoding="utf-8")
    history = sorted((tmp_path / "resident/continuation/operator-answers").glob("*.md"))
    assert any(path.name != "latest.md" for path in history)


@pytest.mark.asyncio
async def test_soft_policy_observation_loaded_from_local_memory_allows_repeat_action(
    tmp_path: Path,
) -> None:
    memory = LocalResidentMemory(tmp_path)
    await memory.write_policy_observation(
        ResidentPolicyObservation(
            subject="soft-allow:research",
            observation="Safe research can continue without asking again.",
            source="operator_answer",
            status="accepted",
        )
    )
    agent = FakeBackendAgent(
        [
            _turn(_outcome("Research candidate tools using local notes.")),
            _turn(_outcome("Sleep until new context appears.")),
        ]
    )
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        policy=ConfigurableResidentPolicy(
            soft_boundaries=(
                ResidentPolicyBoundary(name="research", terms=("research",), hard=False),
            )
        ),
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=2)),
    )

    run = await kernel.run(MANDATE)

    assert run.decisions[0].kind == ContinuationDecisionKind.CONTINUE
    decision_files = sorted((tmp_path / "resident/continuation/policy-decisions").glob("*.md"))
    assert decision_files
    assert "accepted soft allowance: research" in decision_files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_soft_boundary_asks_without_accepted_observation(tmp_path: Path) -> None:
    memory = LocalResidentMemory(tmp_path)
    agent = FakeBackendAgent(
        [_turn(_outcome("Research candidate tools using local notes."))]
    )
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        policy=ConfigurableResidentPolicy(
            soft_boundaries=(
                ResidentPolicyBoundary(name="research", terms=("research",), hard=False),
            )
        ),
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=2)),
    )

    run = await kernel.run(MANDATE)

    assert run.final_decision is not None
    assert run.final_decision.kind == ContinuationDecisionKind.ASK_OPERATOR
    assert "research" in run.final_decision.reason


@pytest.mark.asyncio
async def test_soft_allow_observation_cannot_bypass_hard_boundary(tmp_path: Path) -> None:
    memory = LocalResidentMemory(tmp_path)
    await memory.write_policy_observation(
        ResidentPolicyObservation(
            subject="soft-allow:physical_operation",
            observation="A mistaken soft allowance must not bypass hard gates.",
            source="operator_answer",
            status="accepted",
        )
    )
    agent = FakeBackendAgent([_turn(_outcome("Operate a physical machine."))])
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        policy=ConfigurableResidentPolicy(),
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=2)),
    )

    run = await kernel.run(MANDATE)

    assert run.final_decision is not None
    assert run.final_decision.kind == ContinuationDecisionKind.ASK_OPERATOR
    assert "physical_operation" in run.final_decision.reason


@pytest.mark.asyncio
async def test_contradictory_policy_observations_keep_soft_boundary_as_ask(
    tmp_path: Path,
) -> None:
    memory = LocalResidentMemory(tmp_path)
    await memory.write_policy_observation(
        ResidentPolicyObservation(
            subject="soft-allow:research",
            observation="Safe research can continue.",
            source="operator_answer",
            status="accepted",
        )
    )
    await memory.write_policy_observation(
        ResidentPolicyObservation(
            subject="soft-ask:research",
            observation="Ask before research until the operator reviews this.",
            source="operator_answer",
            status="accepted",
        )
    )
    agent = FakeBackendAgent([_turn(_outcome("Research candidate tools."))])
    kernel = ResidentContinuationKernel(
        agent=agent,
        memory=memory,
        policy=ConfigurableResidentPolicy(
            soft_boundaries=(
                ResidentPolicyBoundary(name="research", terms=("research",), hard=False),
            )
        ),
        budget=ResidentRunBudget(ResidentBudgetLimits(max_turns=2)),
    )

    run = await kernel.run(MANDATE)

    assert run.final_decision is not None
    assert run.final_decision.kind == ContinuationDecisionKind.ASK_OPERATOR
    assert any(
        "contradictory accepted observations keep ask boundary: research" in note
        for note in run.final_decision.calibration_notes
    )


class FakeMimir:
    def __init__(self) -> None:
        self.pages: dict[str, str] = {}

    async def search(self, query: str) -> list[Any]:
        class Meta:
            def __init__(self, path: str) -> None:
                self.path = path
                self.summary = "summary"

        class Page:
            def __init__(self, path: str, content: str) -> None:
                self.meta = Meta(path)
                self.content = content

        return [Page(path, content) for path, content in self.pages.items()]

    async def upsert_page(
        self,
        path: str,
        content: str,
        mimir: str | None = None,
        meta: Any | None = None,
    ) -> None:
        self.pages[path] = content

    async def read_page(self, path: str) -> str:
        try:
            return self.pages[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    async def list_pages(
        self,
        category: str | None = None,
        prefix: str | None = None,
    ) -> list[Any]:
        class Meta:
            def __init__(self, path: str) -> None:
                self.path = path
                self.summary = "summary"

        return [
            Meta(path)
            for path in sorted(self.pages)
            if prefix is None or path.startswith(prefix)
        ]


@pytest.mark.asyncio
async def test_mimir_memory_writes_and_reads_existing_mimir_pages() -> None:
    mimir = FakeMimir()
    memory = MimirResidentState(mimir)  # type: ignore[arg-type]
    record = ResidentTurnRecord(
        turn_index=1,
        prompt="mandate",
        response="response",
        outcome_fields={"selected_next_action": "write note"},
        tool_names=("mimir_write",),
        usage=TokenUsage(input_tokens=1, output_tokens=2),
    )

    ref = await memory.write_turn(record)
    recalled = await memory.recall(MANDATE)

    assert ref in mimir.pages
    assert recalled
    assert recalled[0].path == ref


def test_continuation_kernel_contains_no_domain_specific_playbook_terms() -> None:
    source = Path("src/ravn/resident_continuation.py").read_text(encoding="utf-8").casefold()

    for forbidden in ("kanuck", "inventory", "3d printing", "slicer", "stl", "catalog/product"):
        assert forbidden not in source
