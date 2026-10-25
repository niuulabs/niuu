"""A resident's operator questions reach its owner's notification feed, and their answers
retire them: the resident runtime writes both to its notification outbox."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from niuu.domain.resident_notifications import AttentionInputKind, ResidentNotificationOp
from ravn.adapters.resident_notifications import SqliteResidentNotificationOutbox
from ravn.adapters.resident_state.mimir import LocalResidentState
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


_OPEN_OUTBOXES: list[SqliteResidentNotificationOutbox] = []


@pytest.fixture(autouse=True)
def _close_outboxes() -> Iterator[None]:
    """Close every outbox a test opened; a leaked connection warns in a later test."""
    yield
    while _OPEN_OUTBOXES:
        _OPEN_OUTBOXES.pop().close()


def _outbox(tmp_path) -> SqliteResidentNotificationOutbox:
    outbox = SqliteResidentNotificationOutbox(tmp_path / "openclaw" / "state.db", max_pending=100)
    _OPEN_OUTBOXES.append(outbox)
    return outbox


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


async def test_a_question_to_the_operator_raises_attention_for_the_main_room(tmp_path) -> None:
    outbox = _outbox(tmp_path)
    runtime, _ = _runtime(tmp_path, outbox=outbox)

    await _ask(runtime)

    [entry] = await outbox.pending(limit=10)
    assert entry.op is ResidentNotificationOp.ATTENTION
    assert entry.channel is None, "a resident's own question is asked in its main room"
    assert entry.input_kind is AttentionInputKind.QUESTION
    assert entry.prompt == "May I inspect etcd logs?"
    assert entry.request_id == operator_question_request_id("root-1", "1")


async def test_the_answer_retires_it(tmp_path) -> None:
    outbox = _outbox(tmp_path)
    runtime, queued = _runtime(tmp_path, outbox=outbox)
    await _ask(runtime)

    submitted = await runtime.submit_operator_answer(case_id="root-1", answer="Yes, read-only.")

    assert submitted["queued"] is True and queued
    attention, resolved = await outbox.pending(limit=10)
    assert resolved.op is ResidentNotificationOp.RESOLVED
    assert resolved.resolved_request_ids == [attention.request_id]


async def test_a_directed_reply_in_the_room_is_the_answer(tmp_path) -> None:
    outbox = _outbox(tmp_path)
    runtime, _ = _runtime(tmp_path, outbox=outbox)
    await _ask(runtime)

    assert await runtime.consume_directed_message("Yes.", {"help_context": {}})

    ops = [entry.op for entry in await outbox.pending(limit=10)]
    assert ops == [ResidentNotificationOp.ATTENTION, ResidentNotificationOp.RESOLVED]


async def test_questions_waiting_at_startup_are_raised_once(tmp_path) -> None:
    runtime, _ = _runtime(tmp_path)
    await _ask(runtime)  # asked before this resident had an outbox

    outbox = _outbox(tmp_path)
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
