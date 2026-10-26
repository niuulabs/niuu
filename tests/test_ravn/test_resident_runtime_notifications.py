"""A resident's operator questions reach its owner's notification feed, and their answers
retire them: the resident runtime writes both to its notification outbox."""

from __future__ import annotations

import pytest

from niuu.domain.resident_notifications import AttentionInputKind, ResidentNotificationOp
from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.domain.exceptions import UnboundOperatorReplyError
from ravn.domain.models import AgentTask
from ravn.resident_runtime import ResidentRuntime, operator_question_request_id
from tests.test_ravn.test_resident_runtime import _result, _task


def _runtime(tmp_path, *, outbox=None) -> tuple[ResidentRuntime, list[AgentTask]]:
    queued: list[AgentTask] = []

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime = ResidentRuntime(state=LocalResidentState(tmp_path / "state"))
    runtime.bind_enqueue(enqueue)
    if outbox is not None:
        runtime.bind_notification_outbox(outbox)
    return runtime, queued


async def _ask(runtime: ResidentRuntime, question: str = "May I inspect etcd logs?") -> None:
    await runtime.handle_completed_turn(
        task=_task(),
        prompt="prompt",
        result=_result(
            {
                "continuation": "ask_operator",
                "question": question,
                "reason": "operator approval is required",
            }
        ),
        response_text="response",
    )


async def test_a_question_to_the_operator_raises_attention_for_the_main_room(
    tmp_path, open_outbox
) -> None:
    outbox = open_outbox(tmp_path / "openclaw" / "state.db")
    runtime, _ = _runtime(tmp_path, outbox=outbox)

    await _ask(runtime)

    [entry] = await outbox.pending(limit=10)
    assert entry.op is ResidentNotificationOp.ATTENTION
    assert entry.channel is None, "a resident's own question is asked in its main room"
    assert entry.input_kind is AttentionInputKind.QUESTION
    assert entry.prompt == "May I inspect etcd logs?"
    assert entry.request_id == operator_question_request_id("root-1", "1")


async def test_the_answer_retires_it(tmp_path, open_outbox) -> None:
    outbox = open_outbox(tmp_path / "openclaw" / "state.db")
    runtime, queued = _runtime(tmp_path, outbox=outbox)
    await _ask(runtime)

    submitted = await runtime.submit_operator_answer(case_id="root-1", answer="Yes, read-only.")

    assert submitted["queued"] is True and queued
    attention, resolved = await outbox.pending(limit=10)
    assert resolved.op is ResidentNotificationOp.RESOLVED
    assert resolved.resolved_request_ids == [attention.request_id]


async def test_a_directed_reply_in_the_room_is_the_answer(tmp_path, open_outbox) -> None:
    outbox = open_outbox(tmp_path / "openclaw" / "state.db")
    runtime, _ = _runtime(tmp_path, outbox=outbox)
    await _ask(runtime)

    assert await runtime.consume_directed_message("Yes.", {"help_context": {}})

    ops = [entry.op for entry in await outbox.pending(limit=10)]
    assert ops == [ResidentNotificationOp.ATTENTION, ResidentNotificationOp.RESOLVED]


async def _ask_in(runtime: ResidentRuntime, case_id: str, question: str) -> None:
    await runtime.handle_completed_turn(
        task=_task(task_id=f"task-{case_id}", root_correlation_id=case_id),
        prompt="prompt",
        result=_result({"continuation": "ask_operator", "question": question}),
        response_text="response",
    )


async def test_a_reply_binds_to_the_question_it_replied_to_not_the_latest(tmp_path) -> None:
    """The room keeps only the latest question's context; the replied-to message names
    the exact case, so a reply to an older question still reaches that question."""
    runtime, queued = _runtime(tmp_path)
    await _ask_in(runtime, "case-route", "Which route is authorized?")
    await _ask_in(runtime, "case-scope", "Which scope applies?")

    consumed = await runtime.consume_directed_message(
        "Use GitOps.",
        {
            "help_context": {"resident_case_id": "case-scope"},
            "reply_context": {"help_context": {"resident_case_id": "case-route"}},
            "root_correlation_id": "room-1",
        },
    )

    assert consumed
    assert queued[-1].resident_case_id == "case-route"
    assert await runtime._state.read_operator_needed("case-route") is None
    assert await runtime._state.read_operator_needed("case-scope") is not None


async def test_a_reply_naming_a_case_with_no_open_question_raises(tmp_path) -> None:
    """A reply to a question that was never persisted, or is already closed, must not
    bind to whichever question happens to be open, nor pass as ordinary chat."""
    runtime, queued = _runtime(tmp_path)
    await _ask_in(runtime, "case-open", "Which scope applies?")

    with pytest.raises(UnboundOperatorReplyError) as raised:
        await runtime.consume_directed_message(
            "It is intended behaviour", {"help_context": {"resident_case_id": "case-gone"}}
        )

    assert raised.value.case_id == "case-gone"
    assert queued == []
    assert await runtime._state.read_operator_needed("case-open") is not None


async def test_a_help_context_case_id_binds_the_reply(tmp_path) -> None:
    runtime, queued = _runtime(tmp_path)
    await _ask_in(runtime, "case-route", "Which route is authorized?")
    await _ask_in(runtime, "case-scope", "Which scope applies?")

    assert await runtime.consume_directed_message(
        "Read-only.", {"help_context": {"resident_case_id": "case-scope"}}
    )
    assert queued[-1].resident_case_id == "case-scope"


async def test_a_reply_correlated_to_no_waiting_case_is_ordinary_chat(tmp_path) -> None:
    runtime, queued = _runtime(tmp_path)
    await _ask_in(runtime, "case-route", "Which route is authorized?")
    await _ask_in(runtime, "case-scope", "Which scope applies?")

    assert not await runtime.consume_directed_message(
        "Hello", {"help_context": {"root_correlation_id": "room-session"}}
    )
    assert queued == []


async def test_questions_waiting_at_startup_are_raised_once(tmp_path, open_outbox) -> None:
    runtime, _ = _runtime(tmp_path)
    await _ask(runtime)  # asked before this resident had an outbox

    outbox = open_outbox(tmp_path / "openclaw" / "state.db")
    restarted, _ = _runtime(tmp_path, outbox=outbox)
    assert await restarted.raise_pending_attention() == 1
    assert await restarted.raise_pending_attention() == 0, "a raised question is not repeated"

    [entry] = await outbox.pending(limit=10)
    assert entry.request_id == operator_question_request_id("root-1", "1")
    assert entry.prompt == "May I inspect etcd logs?"


async def test_without_an_outbox_nothing_is_raised(tmp_path) -> None:
    runtime, _ = _runtime(tmp_path)
    await _ask(runtime)
    assert await runtime.raise_pending_attention() == 0
    await runtime.submit_operator_answer(case_id="root-1", answer="ok")


def test_request_ids_distinguish_a_case_asking_again() -> None:
    assert operator_question_request_id("c", "1") != operator_question_request_id("c", "2")
    assert operator_question_request_id("c", "") == "operator:c:0"


async def test_a_question_asked_on_a_spent_budget_still_takes_its_reply(tmp_path) -> None:
    """Regin asked on the last turn of its case's budget: the question reached the
    operator, but nothing was persisted, so the reply became a separate task and the
    case asked again. The question must stay open and its reply must resume it."""
    queued: list[AgentTask] = []

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        return True

    runtime = ResidentRuntime(state=LocalResidentState(tmp_path / "state"))
    runtime.bind_enqueue(enqueue)
    case_id = "resident-stewardship-20261026T002939Z"
    disposition = await runtime.handle_completed_turn(
        task=_task(
            task_id="task_resident_home_1",
            root_correlation_id="regin-operator-room",
            resident_case_id=case_id,
            resident_turn_index=3,
        ),
        prompt="prompt",
        result=_result(
            {
                "continuation": "ask_operator",
                "question": "Is the 30-minute auto-forward intended?",
                "next_action_timing": "operator_input",
            }
        ),
        response_text="response",
    )
    assert disposition.case_id == case_id

    assert await runtime.consume_directed_message(
        "It is intended behaviour",
        {
            "help_context": {"resident_case_id": case_id},
            "root_correlation_id": "regin-operator-room",
        },
    )
    [resumed] = queued
    assert resumed.resident_case_id == case_id
    assert resumed.resident_turn_index == 4
    assert await runtime._state.read_operator_needed(case_id) is None
