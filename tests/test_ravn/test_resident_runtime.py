from __future__ import annotations

from types import SimpleNamespace

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.config import InitiativeConfig, Settings
from ravn.domain.models import AgentTask, OutputMode, TokenUsage, ToolCall, ToolResult, TurnResult
from ravn.domain.resident_continuation import (
    ContinuationDecisionKind,
    ResidentWorkingStateRecord,
    validate_resident_working_state,
)
from ravn.drive_loop import DriveLoop
from ravn.resident_inbox import MimirResidentInbox, ResidentInboxStatus
from ravn.resident_runtime import ResidentRuntime


def _result(
    fields: dict,
    *,
    tools: tuple[str, ...] = (),
    tool_outputs: dict[str, str] | None = None,
    outcome_valid: bool | None = None,
) -> TurnResult:
    tool_outputs = tool_outputs or {}
    episode = SimpleNamespace(structured_outcome=fields)
    if outcome_valid is not None:
        episode.outcome_valid = outcome_valid
    return TurnResult(
        response="resident response",
        tool_calls=[ToolCall(id=f"call-{name}", name=name, input={}) for name in tools],
        tool_results=[
            ToolResult(tool_call_id=f"call-{name}", content=content)
            for name, content in tool_outputs.items()
        ],
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        episode=episode,
    )


def _task(**overrides) -> AgentTask:
    values = {
        "task_id": "task-initial",
        "title": "resident case",
        "initiative_context": "understand the environment",
        "triggered_by": "test",
        "output_mode": OutputMode.AMBIENT,
        "persona": "domain-drive",
        "root_correlation_id": "root-1",
    }
    values.update(overrides)
    return AgentTask(**values)


def test_working_state_rejects_an_unbounded_event_log() -> None:
    state = {
        "observations": [f"observation {index}" for index in range(6)],
        "hypotheses": [],
        "unknowns": [],
        "capability_gaps": [],
        "attempts": [],
    }

    assert validate_resident_working_state(state) == [
        "working_state.observations has 6 entries; maximum is 5"
    ]


def test_working_state_rejects_oversized_entries() -> None:
    state = {
        "observations": ["x" * 501],
        "hypotheses": [],
        "unknowns": [],
        "capability_gaps": [],
        "attempts": [],
    }

    assert validate_resident_working_state(state) == [
        "working_state.observations[0] exceeds 500 characters"
    ]


@pytest.mark.asyncio
async def test_selected_action_is_persisted_without_queueing_prose_as_work(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    queued: list[AgentTask] = []

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime = ResidentRuntime(state=state, max_turns=3)
    runtime.bind_enqueue(enqueue)
    disposition = await runtime.handle_completed_turn(
        task=_task(),
        prompt="effective prompt",
        result=_result(
            {
                "continuation": "continue",
                "selected_next_action": "inspect the deployment configuration",
                "next_action_timing": "immediate",
                "rationale": "configuration determines the environment boundary",
            },
            tools=("file_read",),
        ),
        response_text="resident response",
    )

    assert disposition.kind is ContinuationDecisionKind.STOP
    assert disposition.case_id == "root-1"
    assert "free-text immediate continuation is unsupported" in disposition.reason
    assert queued == []
    turn_text = (tmp_path / disposition.turn_ref).read_text()
    assert "root_correlation_id: root-1" in turn_text
    assert "tools_used: file_read" in turn_text
    assert "inspect the deployment configuration" in turn_text


@pytest.mark.asyncio
async def test_transport_control_is_not_queued_as_a_resident_action(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    queued: list[AgentTask] = []
    runtime = ResidentRuntime(state=state)

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime.bind_enqueue(enqueue)

    disposition = await runtime.handle_completed_turn(
        task=_task(),
        prompt="watch for another event",
        result=_result(
            {
                "continuation": "continue",
                "selected_next_action": "continue",
            }
        ),
        response_text="no immediate work is possible",
    )

    assert disposition.kind is ContinuationDecisionKind.STOP
    assert "free-text immediate continuation is unsupported" in disposition.reason
    assert queued == []


@pytest.mark.asyncio
async def test_sleep_waits_for_an_external_wake_without_queueing(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    queued: list[AgentTask] = []
    runtime = ResidentRuntime(state=state)

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime.bind_enqueue(enqueue)
    disposition = await runtime.handle_completed_turn(
        task=_task(),
        prompt="wait for another event",
        result=_result(
            {
                "continuation": "sleep",
                "selected_next_action": "await the next observation",
                "next_action_timing": "external_event",
            }
        ),
        response_text="future evidence is required",
    )

    assert disposition.kind is ContinuationDecisionKind.SLEEP
    assert disposition.reason == "model selected sleep"
    assert queued == []


@pytest.mark.asyncio
async def test_missing_action_timing_is_not_queued_as_a_continuation(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    queued: list[AgentTask] = []
    runtime = ResidentRuntime(state=state)

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime.bind_enqueue(enqueue)
    disposition = await runtime.handle_completed_turn(
        task=_task(),
        prompt="inspect the source",
        result=_result(
            {
                "continuation": "continue",
                "selected_next_action": "inspect the source",
            }
        ),
        response_text="the response omitted its action timing",
    )

    assert disposition.kind is ContinuationDecisionKind.STOP
    assert "free-text immediate continuation is unsupported" in disposition.reason
    assert queued == []


@pytest.mark.asyncio
async def test_selected_action_without_continuation_control_is_not_queued(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    queued: list[AgentTask] = []
    runtime = ResidentRuntime(state=state)

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime.bind_enqueue(enqueue)
    disposition = await runtime.handle_completed_turn(
        task=_task(),
        prompt="inspect the source",
        result=_result(
            {
                "selected_next_action": "inspect the source",
                "next_action_timing": "immediate",
            }
        ),
        response_text="the response omitted continuation control",
    )

    assert disposition.kind is ContinuationDecisionKind.STOP
    assert disposition.reason == "selected next action recorded without a wake request"
    assert queued == []


@pytest.mark.asyncio
async def test_tool_result_is_durable_without_creating_a_followup_task(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    queued: list[AgentTask] = []

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime = ResidentRuntime(
        state=state,
        context_max_chars=1000,
        tool_result_max_chars=5000,
    )
    runtime.bind_enqueue(enqueue)
    disposition = await runtime.handle_completed_turn(
        task=_task(),
        prompt="inspect the peer before choosing\n" + ("historical prompt material " * 1000),
        result=_result(
            {
                "continuation": "continue",
                "selected_next_action": "poll peer task peer-42",
                "next_action_timing": "immediate",
            },
            tools=("a2a_task",),
            tool_outputs={
                "a2a_task": '{"task_id":"peer-42","state":"WORKING","detail":"'
                + ("tool detail " * 1000)
                + '"}'
            },
        ),
        response_text="The peer is still working.",
    )

    durable = await state.read(disposition.turn_ref)
    assert durable is not None
    assert queued == []
    assert '"task_id":"peer-42"' in durable.content
    assert "… (truncated)" in durable.content


@pytest.mark.asyncio
async def test_working_state_is_reused_by_a_new_runtime_after_restart(tmp_path) -> None:
    first_state = LocalResidentState(tmp_path)
    first_runtime = ResidentRuntime(state=first_state, resident_id="resident-alpha")
    first_task = _task(root_correlation_id="event-a")

    initial_context = await first_runtime.prepare_context(first_task)
    assert "No prior resident working state exists yet" in initial_context

    await first_runtime.handle_completed_turn(
        task=first_task,
        prompt=initial_context,
        result=_result(
            {
                "continuation": "stop",
                "signal_refs": ["signal-a"],
                "working_state": {
                    "observations": ["signal-a introduced device Raven"],
                    "hypotheses": ["Raven may expose a control surface"],
                    "unknowns": ["how Raven can be inspected"],
                    "capability_gaps": ["no addressable source capability"],
                    "attempts": ["capability discovery returned no matching peer"],
                },
            }
        ),
        response_text="record a revisable model",
    )

    restarted_runtime = ResidentRuntime(
        state=LocalResidentState(tmp_path),
        resident_id="resident-alpha",
    )
    second_context = await restarted_runtime.prepare_context(
        _task(
            task_id="task-second",
            root_correlation_id="event-b",
            initiative_context="A different generic event arrived.",
        )
    )

    assert "A different generic event arrived." in second_context
    assert "resident/continuation/working-state/resident-alpha.md" in second_context
    assert "signal-a introduced device Raven" in second_context
    assert "Raven may expose a control surface" in second_context
    assert "no addressable source capability" in second_context
    assert "Re-evaluate it against the new observations" in second_context
    assert "opaque audit identifiers" in second_context
    assert "not workspace paths" in second_context
    assert "will not turn a prose" in second_context
    assert "Use available tools when they can materially reduce uncertainty" in second_context
    assert "Do not call a tool merely to demonstrate tool use" in second_context


@pytest.mark.asyncio
async def test_missing_working_state_does_not_erase_prior_state(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    await state.write_working_state(
        ResidentWorkingStateRecord(
            resident_id="resident-alpha",
            state={"unknowns": ["operator intent is unknown"]},
            source_turn_ref="turn-a",
            source_case_id="case-a",
            source_task_id="task-a",
        )
    )
    runtime = ResidentRuntime(state=state, resident_id="resident-alpha")

    await runtime.handle_completed_turn(
        task=_task(root_correlation_id="event-b"),
        prompt="new event",
        result=_result({"continuation": "stop"}),
        response_text="no explicit working-state update",
    )

    context = await runtime.prepare_context(
        _task(task_id="task-third", root_correlation_id="event-c")
    )
    assert "operator intent is unknown" in context


@pytest.mark.asyncio
async def test_invalid_partial_working_state_does_not_replace_prior_state(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    await state.write_working_state(
        ResidentWorkingStateRecord(
            resident_id="resident-alpha",
            state={
                "observations": ["signal-a"],
                "hypotheses": [],
                "unknowns": ["operator intent is unknown"],
                "capability_gaps": [],
                "attempts": [],
            },
            source_turn_ref="turn-a",
            source_case_id="case-a",
            source_task_id="task-a",
        )
    )
    runtime = ResidentRuntime(state=state, resident_id="resident-alpha")

    await runtime.handle_completed_turn(
        task=_task(root_correlation_id="event-b"),
        prompt="new event",
        result=_result(
            {
                "continuation": "stop",
                "working_state": {"observations": ["signal-b", None]},
            }
        ),
        response_text="incomplete snapshot",
    )

    context = await runtime.prepare_context(
        _task(task_id="task-third", root_correlation_id="event-c")
    )
    assert "operator intent is unknown" in context
    assert "signal-b" not in context
    assert validate_resident_working_state({"observations": ["signal-b", None]})


@pytest.mark.asyncio
async def test_invalid_outcome_cannot_replace_state_or_queue_a_continuation(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    prior_state = {
        "observations": ["signal-a"],
        "hypotheses": [],
        "unknowns": ["cause unknown"],
        "capability_gaps": [],
        "attempts": [],
    }
    await state.write_working_state(
        ResidentWorkingStateRecord(
            resident_id="resident-alpha",
            state=prior_state,
            source_turn_ref="turn-a",
            source_case_id="case-a",
            source_task_id="task-a",
        )
    )
    queued: list[AgentTask] = []
    runtime = ResidentRuntime(state=state, resident_id="resident-alpha")

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime.bind_enqueue(enqueue)
    disposition = await runtime.handle_completed_turn(
        task=_task(root_correlation_id="event-b"),
        prompt="invalid contract",
        result=_result(
            {
                "continuation": "continue",
                "selected_next_action": "inspect the source",
                "next_action_timing": "immediate",
                "working_state": {
                    "observations": ["unsupported replacement"],
                    "hypotheses": [],
                    "unknowns": [],
                    "capability_gaps": [],
                    "attempts": [],
                },
            },
            outcome_valid=False,
        ),
        response_text="schema-invalid response",
    )

    assert disposition.kind is ContinuationDecisionKind.STOP
    assert disposition.reason == "resident outcome contract was invalid"
    assert queued == []
    durable_state = await state.read_working_state("resident-alpha")
    assert durable_state is not None
    assert "signal-a" in durable_state.content
    assert "unsupported replacement" not in durable_state.content


@pytest.mark.asyncio
async def test_budget_stops_another_operator_round_trip(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    queued: list[AgentTask] = []

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime = ResidentRuntime(state=state, max_turns=1)
    runtime.bind_enqueue(enqueue)
    disposition = await runtime.handle_completed_turn(
        task=_task(),
        prompt="prompt",
        result=_result(
            {
                "continuation": "ask_operator",
                "question": "Which environment is in scope?",
                "next_action_timing": "operator_input",
            }
        ),
        response_text="response",
    )

    assert disposition.kind is ContinuationDecisionKind.STOP
    assert "turn budget" in disposition.reason
    assert queued == []


@pytest.mark.asyncio
async def test_explicit_outcome_routes_operator_question_without_an_episode(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    runtime = ResidentRuntime(state=state)
    result = TurnResult(
        response="operator input is required",
        tool_calls=[],
        tool_results=[],
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )

    disposition = await runtime.handle_completed_turn(
        task=_task(),
        prompt="prompt",
        result=result,
        response_text=result.response,
        outcome_fields={
            "verdict": "help_needed",
            "continuation": "ask_operator",
            "question": "Which scope is authorized?",
            "next_action_timing": "operator_input",
        },
        outcome_valid=True,
    )

    assert result.episode is None
    assert disposition.kind is ContinuationDecisionKind.ASK_OPERATOR
    assert disposition.question == "Which scope is authorized?"
    assert await state.read_operator_needed("root-1") is not None


@pytest.mark.asyncio
async def test_home_trigger_keeps_only_one_wake_inflight(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    state = LocalResidentState(tmp_path / "state")
    runtime = ResidentRuntime(state=state, inbox=inbox)
    await inbox.write_directed_message(
        content="First observation",
        metadata={"message_id": "home-1"},
    )
    first = await runtime.next_home_task(
        limit=5,
        persona="domain-drive",
        output_mode=OutputMode.AMBIENT,
    )
    assert first is not None

    await inbox.write_directed_message(
        content="Second observation while the first wake is active",
        metadata={"message_id": "home-2"},
    )
    assert (
        await runtime.next_home_task(
            limit=5,
            persona="domain-drive",
            output_mode=OutputMode.AMBIENT,
        )
        is None
    )

    await runtime.handle_completed_turn(
        task=first,
        prompt="home prompt",
        result=_result({"continuation": "stop"}),
        response_text="first home turn complete",
    )
    second = await runtime.next_home_task(
        limit=5,
        persona="domain-drive",
        output_mode=OutputMode.AMBIENT,
    )
    assert second is not None
    assert len(second.resident_inbox_refs) == 1
    assert second.resident_inbox_refs != first.resident_inbox_refs


@pytest.mark.asyncio
async def test_operator_answer_resumes_same_case_and_is_consumed_after_success(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    queued: list[AgentTask] = []

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime = ResidentRuntime(state=state)
    runtime.bind_enqueue(enqueue)
    disposition = await runtime.handle_completed_turn(
        task=_task(),
        prompt="prompt",
        result=_result(
            {
                "verdict": "help_needed",
                "open_questions": ["Which customer environment is in scope?"],
                "reason": "operator intent changes the safe boundary",
            }
        ),
        response_text="response",
    )

    assert disposition.kind is ContinuationDecisionKind.ASK_OPERATOR
    pending = await runtime.pending_questions()
    assert pending[0]["case_id"] == "root-1"

    submitted = await runtime.submit_operator_answer(
        case_id="root-1",
        answer="Use the staging environment only.",
    )
    assert submitted["queued"] is True
    resume = queued[-1]
    assert resume.resident_case_id == "root-1"
    assert resume.root_correlation_id == "root-1"
    assert resume.resident_turn_index == 2
    assert "## Prior-turn handoff" in resume.initiative_context
    assert "## Response\n\nresponse" not in resume.initiative_context
    assert "Selected next action: none" in resume.initiative_context
    assert await state.read_operator_answer("root-1") is not None

    await runtime.handle_completed_turn(
        task=resume,
        prompt="resume prompt",
        result=_result({"continuation": "stop"}),
        response_text="completed with staging scope",
    )
    assert await state.read_operator_answer("root-1") is None


@pytest.mark.asyncio
async def test_operator_answer_preserves_suspended_a2a_build_handoff(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    queued: list[AgentTask] = []

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime = ResidentRuntime(state=state, tool_result_max_chars=5000)
    runtime.bind_enqueue(enqueue)
    await runtime.handle_completed_turn(
        task=_task(),
        prompt="commission the missing capability",
        result=_result(
            {
                "continuation": "ask_operator",
                "question": "Which namespace should the remote builder target?",
                "next_action_timing": "operator_input",
            },
            tools=("build_tool",),
            tool_outputs={
                "build_tool": (
                    '{"status":"input_required",'
                    '"task_id":"tool-build-peer-42",'
                    '"input_kind":"question",'
                    '"question":"Which namespace should the remote builder target?",'
                    '"reply_metadata":{"requestId":"help-17"},'
                    '"resume_with":{"continuation_task_id":"tool-build-peer-42",'
                    '"continuation_answer":"<answer>"}}'
                )
            },
        ),
        response_text="The remote A2A task needs operator-owned scope.",
    )

    submitted = await runtime.submit_operator_answer(
        case_id="root-1",
        answer="Use the staging namespace only.",
    )

    assert submitted["queued"] is True
    resume = queued[-1]
    assert resume.triggered_by == "resident:operator_answer"
    assert "Operator answer: Use the staging namespace only." in resume.initiative_context
    assert '"task_id":"tool-build-peer-42"' in resume.initiative_context
    assert '"requestId":"help-17"' in resume.initiative_context
    assert '"continuation_task_id":"tool-build-peer-42"' in resume.initiative_context


@pytest.mark.asyncio
async def test_operator_answer_fails_if_exact_parent_turn_is_unavailable(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    runtime = ResidentRuntime(state=state)
    runtime.bind_enqueue(lambda _task: pytest.fail("resume must not be queued"))
    disposition = await runtime.handle_completed_turn(
        task=_task(),
        prompt="prompt",
        result=_result(
            {
                "verdict": "help_needed",
                "question": "Which environment is in scope?",
            }
        ),
        response_text="response",
    )
    (tmp_path / disposition.turn_ref).unlink()

    with pytest.raises(RuntimeError, match="parent turn is not readable"):
        await runtime.submit_operator_answer(case_id="root-1", answer="staging")


@pytest.mark.asyncio
async def test_home_turn_reads_new_records_and_acknowledges_only_after_record(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    state = LocalResidentState(tmp_path / "state")
    await inbox.write_directed_message(
        content="Please investigate the staging rollout",
        metadata={
            "telegram_message_id": "501",
            "source_context": {"service": "deployer", "state": "stalled"},
        },
    )
    runtime = ResidentRuntime(
        state=state,
        inbox=inbox,
        resident_personality="Patient investigator",
        charter="Understand this environment and close material knowledge gaps.",
    )

    task = await runtime.next_home_task(
        limit=5,
        persona="domain-drive",
        output_mode=OutputMode.AMBIENT,
    )
    assert task is not None
    assert task.resident_inbox_refs
    assert "inbox_ref=resident/inbox/signals/" in task.initiative_context
    assert "Bounded raw payload:" in task.initiative_context
    assert '"service": "deployer"' in task.initiative_context
    assert '"state": "stalled"' in task.initiative_context
    assert "Personality: Patient investigator" in task.initiative_context
    assert (
        "Charter: Understand this environment and close material knowledge gaps."
        in task.initiative_context
    )
    assert len(await inbox.list_signals(status=ResidentInboxStatus.NEW.value)) == 1

    await runtime.handle_completed_turn(
        task=task,
        prompt="home prompt",
        result=_result({"continuation": "stop"}),
        response_text="home turn complete",
    )
    assert await inbox.list_signals(status=ResidentInboxStatus.NEW.value) == []
    remembered = await inbox.list_signals(status=ResidentInboxStatus.REMEMBERED.value)
    assert len(remembered) == 1


@pytest.mark.asyncio
async def test_home_turn_continues_the_source_signal_trace(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    state = LocalResidentState(tmp_path / "state")
    trace_context = {"traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"}
    await inbox.write_event(
        SimpleNamespace(
            event_id="evt-traced",
            event_type="environment.signal",
            correlation_id="corr-traced",
            trace_context=trace_context,
            timestamp="2026-07-21T12:33:00Z",
            summary="Printer disconnected",
            payload={"data": {"source_id": "workshop"}},
        )
    )

    task = await ResidentRuntime(state=state, inbox=inbox).next_home_task(
        limit=5,
        persona="domain-drive",
        output_mode=OutputMode.AMBIENT,
    )

    assert task is not None
    assert task.trace_context == trace_context


@pytest.mark.asyncio
async def test_invalid_home_turn_leaves_inbox_observation_unacknowledged(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    state = LocalResidentState(tmp_path / "state")
    await inbox.write_directed_message(
        content="Please investigate the staging rollout",
        metadata={"telegram_message_id": "502"},
    )
    runtime = ResidentRuntime(state=state, inbox=inbox)
    task = await runtime.next_home_task(
        limit=5,
        persona="domain-drive",
        output_mode=OutputMode.AMBIENT,
    )
    assert task is not None

    await runtime.handle_completed_turn(
        task=task,
        prompt="invalid home prompt",
        result=_result(
            {"continuation": "stop"},
            outcome_valid=False,
        ),
        response_text="schema-invalid home turn",
    )

    assert len(await inbox.list_signals(status=ResidentInboxStatus.NEW.value)) == 1
    assert await inbox.list_signals(status=ResidentInboxStatus.REMEMBERED.value) == []


@pytest.mark.asyncio
async def test_drive_loop_sends_completed_turn_to_resident_runtime(tmp_path) -> None:
    state = LocalResidentState(tmp_path / "state")
    await state.write_working_state(
        ResidentWorkingStateRecord(
            resident_id="resident",
            state={"hypotheses": ["prior state reaches the execution prompt"]},
            source_turn_ref="turn-before-restart",
            source_case_id="case-before-restart",
            source_task_id="task-before-restart",
        )
    )
    runtime = ResidentRuntime(state=state, resident_id="resident")
    prompts: list[str] = []

    class Agent:
        async def run_turn(self, prompt: str) -> TurnResult:
            prompts.append(prompt)
            return _result({"continuation": "stop"}, tools=("file_read",))

    settings = Settings(
        initiative=InitiativeConfig(
            enabled=True,
            max_concurrent_tasks=1,
            queue_journal_path=str(tmp_path / "queue.json"),
        )
    )
    loop = DriveLoop(
        agent_factory=lambda *_args: Agent(),
        config=settings.initiative,
        settings=settings,
    )
    loop.set_resident_runtime(runtime)

    await loop._run_task(_task())

    refs = await state.list_refs("resident/continuation/cases/root-1")
    assert any("/turns/" in ref for ref in refs)
    assert any(ref.endswith("/budget/latest.md") for ref in refs)
    assert "prior state reaches the execution prompt" in prompts[0]
