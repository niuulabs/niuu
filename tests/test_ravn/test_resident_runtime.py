from __future__ import annotations

from types import SimpleNamespace

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.config import InitiativeConfig, Settings
from ravn.domain.models import AgentTask, OutputMode, TokenUsage, ToolCall, ToolResult, TurnResult
from ravn.domain.resident_continuation import ContinuationDecisionKind
from ravn.drive_loop import DriveLoop
from ravn.resident_inbox import MimirResidentInbox, ResidentInboxStatus
from ravn.resident_runtime import ResidentRuntime


def _result(
    fields: dict,
    *,
    tools: tuple[str, ...] = (),
    tool_outputs: dict[str, str] | None = None,
) -> TurnResult:
    tool_outputs = tool_outputs or {}
    return TurnResult(
        response="resident response",
        tool_calls=[ToolCall(id=f"call-{name}", name=name, input={}) for name in tools],
        tool_results=[
            ToolResult(tool_call_id=f"call-{name}", content=content)
            for name, content in tool_outputs.items()
        ],
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        episode=SimpleNamespace(structured_outcome=fields),
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


@pytest.mark.asyncio
async def test_selected_action_is_persisted_and_queued_with_same_case(tmp_path) -> None:
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
                "selected_next_action": "inspect the deployment configuration",
                "rationale": "configuration determines the environment boundary",
            },
            tools=("file_read",),
        ),
        response_text="resident response",
    )

    assert disposition.kind is ContinuationDecisionKind.CONTINUE
    assert disposition.case_id == "root-1"
    assert len(queued) == 1
    continuation = queued[0]
    assert continuation.resident_case_id == "root-1"
    assert continuation.root_correlation_id == "root-1"
    assert continuation.resident_turn_index == 2
    assert continuation.resident_input_tokens == 10
    assert "inspect the deployment configuration" in continuation.initiative_context
    turn_text = (tmp_path / disposition.turn_ref).read_text()
    assert "root_correlation_id: root-1" in turn_text
    assert "tools_used: file_read" in turn_text


@pytest.mark.asyncio
async def test_continuation_rehydrates_exact_durable_turn_and_tool_state(tmp_path) -> None:
    state = LocalResidentState(tmp_path)
    queued: list[AgentTask] = []

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime = ResidentRuntime(state=state)
    runtime.bind_enqueue(enqueue)
    disposition = await runtime.handle_completed_turn(
        task=_task(),
        prompt="inspect the peer before choosing",
        result=_result(
            {"selected_next_action": "poll peer task peer-42"},
            tools=("a2a_task",),
            tool_outputs={"a2a_task": '{"task_id":"peer-42","state":"WORKING"}'},
        ),
        response_text="The peer is still working.",
    )

    durable = await state.read(disposition.turn_ref)
    assert durable is not None
    continuation = queued[0]
    assert "## Durable prior turn" in continuation.initiative_context
    assert "inspect the peer before choosing" in continuation.initiative_context
    assert '"task_id":"peer-42"' in continuation.initiative_context


@pytest.mark.asyncio
async def test_budget_stops_selected_action_without_enqueuing(tmp_path) -> None:
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
        result=_result({"selected_next_action": "inspect more evidence"}),
        response_text="response",
    )

    assert disposition.kind is ContinuationDecisionKind.STOP
    assert "turn budget" in disposition.reason
    assert queued == []


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
    assert "## Durable prior turn" in resume.initiative_context
    assert "## Response\n\nresponse" in resume.initiative_context
    assert await state.read_operator_answer("root-1") is not None

    await runtime.handle_completed_turn(
        task=resume,
        prompt="resume prompt",
        result=_result({"continuation": "stop"}),
        response_text="completed with staging scope",
    )
    assert await state.read_operator_answer("root-1") is None


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
        metadata={"telegram_message_id": "501"},
    )
    runtime = ResidentRuntime(state=state, inbox=inbox)

    task = await runtime.next_home_task(
        limit=5,
        persona="domain-drive",
        output_mode=OutputMode.AMBIENT,
    )
    assert task is not None
    assert task.resident_inbox_refs
    assert "inbox_ref=resident/inbox/signals/" in task.initiative_context
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
async def test_drive_loop_sends_completed_turn_to_resident_runtime(tmp_path) -> None:
    state = LocalResidentState(tmp_path / "state")
    runtime = ResidentRuntime(state=state)

    class Agent:
        async def run_turn(self, _prompt: str) -> TurnResult:
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
