"""Regressions from the Lexi iOS Astra conversation on September 8, 2026."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_skuld.test_codex_ws_transport import FakeWebSocket, _make_transport


@pytest.mark.parametrize("options", [None, [], ["Amber", "Blue"]])
async def test_async_question_options_allow_freeform_without_closing_reader(tmp_path, options):
    transport = _make_transport(tmp_path)
    transport._thread_id = "original-thread"
    transport._current_turn_id = "review-turn"
    transport._active_user_prompt = "Review the project and keep working"
    transport._alive = True
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))
    transport._ws = FakeWebSocket()
    transport._ws.inject(
        {
            "method": "item/completed",
            "params": {
                "threadId": "original-thread",
                "turnId": "review-turn",
                "item": {
                    "type": "agentMessage",
                    "id": "clarify-host",
                    "delivery": "async",
                    "questions": [{"title": "Which URL should I investigate?", "options": options}],
                },
            },
        }
    )
    transport._ws.inject(
        {
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "original-thread",
                "turnId": "review-turn",
                "delta": "The review continued after the question.",
            },
        }
    )
    transport._ws.inject(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "original-thread",
                "turn": {"id": "review-turn", "status": "completed"},
            },
        }
    )

    await transport._receive_loop()

    question = next(event for event in events if event["type"] == "ask_user_question")
    assert [option["label"] for option in question["questions"][0]["options"]] == (options or [])
    assert any(
        event.get("delta", {}).get("text") == "The review continued after the question."
        for event in events
    )
    results = [event for event in events if event["type"] == "result"]
    assert len(results) == 1
    assert results[0]["stop_reason"] == "end_turn"
    assert not results[0]["is_error"]


async def test_missing_async_options_accepts_explicit_freeform_answer(tmp_path):
    transport = _make_transport(tmp_path)
    transport._thread_id = "original-thread"
    transport._current_turn_id = "review-turn"
    transport._send_rpc = AsyncMock(return_value={})
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))
    await transport._handle_item_completed(
        {
            "type": "agentMessage",
            "id": "clarify-host",
            "delivery": "async",
            "questions": [{"title": "Which URL should I investigate?"}],
        }
    )
    question = events[-1]

    await transport.send_control(
        "ask_user_answer",
        request_id=question["request_id"],
        answers=[{"question_id": "clarify-host:0", "answer": "http://localhost:8080"}],
    )

    method, params = transport._send_rpc.call_args.args
    assert method == "turn/steer"
    assert params["threadId"] == "original-thread"
    assert params["input"][0]["text"].endswith("http://localhost:8080")
    assert events[-1]["type"] == "ask_user_resolved"


async def test_processing_failure_retains_cause_in_durable_result_and_pending_rpc(tmp_path):
    transport = _make_transport(tmp_path)
    transport._thread_id = "original-thread"
    transport._current_turn_id = "review-turn"
    transport._alive = True
    transport._ws = FakeWebSocket()
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))
    pending_rpc = asyncio.get_running_loop().create_future()
    transport._pending[31] = pending_rpc
    transport._ws.inject(
        {
            "method": "item/completed",
            "params": {"threadId": "original-thread", "item": None},
        }
    )

    await transport._receive_loop()

    results = [event for event in events if event["type"] == "result"]
    assert len(results) == 1
    assert results[0]["is_error"]
    assert "item/completed (AttributeError)" in results[0]["error"]
    assert "NoneType" in results[0]["error"]
    with pytest.raises(RuntimeError, match="item/completed \\(AttributeError\\)"):
        await pending_rpc
    assert transport.session_id == "original-thread"
    assert not transport.is_alive


async def test_blocking_freeform_question_normalizes_nullable_options(tmp_path):
    transport = _make_transport(tmp_path)
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))
    transport._send_rpc_response = AsyncMock()
    await transport._handle_server_request(
        {
            "id": 31,
            "method": "item/tool/requestUserInput",
            "params": {
                "questions": [
                    {"id": "host", "header": "Host", "question": "Which host?", "options": None}
                ]
            },
        }
    )
    assert events[-1]["questions"][0]["options"] == []

    await transport.send_control(
        "ask_user_answer",
        request_id=events[-1]["request_id"],
        answers=[{"question_id": "host", "answer": "localhost"}],
    )

    transport._send_rpc_response.assert_awaited_once_with(
        31, {"answers": {"host": {"answers": ["localhost"]}}}
    )


async def test_restart_after_disconnect_resumes_history_and_reaps_old_process(tmp_path):
    transport = _make_transport(tmp_path, initial_prompt="Original review request")
    transport._thread_id = "original-thread"
    transport._alive = False
    old_process = MagicMock()
    old_socket = MagicMock(close=AsyncMock())
    transport._process = old_process
    transport._ws = old_socket
    transport._receive_task = asyncio.create_task(asyncio.sleep(0))
    await transport._receive_task
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))
    lifecycle = []

    async def stop_process(process):
        assert process is old_process
        lifecycle.append("old process stopped")

    async def spawn():
        assert transport._process is None
        assert transport._ws is None
        assert transport._receive_task is None
        lifecycle.append("new process started")

    transport._spawn_app_server = AsyncMock(side_effect=spawn)
    transport._connect_ws = AsyncMock()
    transport._send_notification = AsyncMock()
    transport._send_rpc = AsyncMock(side_effect=[{}, {"thread": {"id": "original-thread"}}])
    transport.send_message = AsyncMock()
    with patch("skuld.transports.codex_ws._stop_process", side_effect=stop_process):
        await transport.start()

    assert lifecycle == ["old process stopped", "new process started"]
    old_socket.close.assert_awaited_once()
    methods = [call.args[0] for call in transport._send_rpc.call_args_list]
    assert methods == ["initialize", "thread/resume"]
    assert transport._send_rpc.call_args.args[1]["threadId"] == "original-thread"
    assert transport.session_id == "original-thread"
    transport.send_message.assert_not_awaited()
    assert events[-1]["session_id"] == "original-thread"


@pytest.mark.parametrize("imported", [True, False])
async def test_failed_resume_keeps_history_identity_and_never_falls_back(tmp_path, imported):
    transport = _make_transport(tmp_path, resume_session_id="original-thread" if imported else "")
    if not imported:
        transport._thread_id = "original-thread"
    transport._spawn_app_server = AsyncMock()
    transport._connect_ws = AsyncMock(side_effect=RuntimeError("connection refused"))
    transport._start_fallback_transport = AsyncMock()

    with pytest.raises(RuntimeError, match="resume the existing Codex conversation"):
        await transport.start()

    transport._start_fallback_transport.assert_not_awaited()
    assert transport._resume_session_id == "original-thread"
    assert not transport.is_alive


async def test_resume_rejects_unexpected_replacement_thread(tmp_path):
    transport = _make_transport(tmp_path, resume_session_id="original-thread")
    transport._send_rpc = AsyncMock(side_effect=[{}, {"thread": {"id": "replacement-thread"}}])
    transport._send_notification = AsyncMock()
    transport._emit = AsyncMock()

    with pytest.raises(RuntimeError, match="different conversation"):
        await transport._handshake()

    transport._emit.assert_not_awaited()
    assert transport._thread_id is None


@pytest.mark.parametrize("resuming", [False, True])
async def test_worker_thread_started_cannot_take_over_parent_input_or_completion(
    tmp_path, resuming
):
    transport = _make_transport(tmp_path, resume_session_id="original-thread" if resuming else "")
    if not resuming:
        transport._thread_id = "original-thread"
    transport._current_turn_id = "original-turn"
    transport._send_rpc = AsyncMock(return_value={})
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))

    await transport._handle_server_message(
        {"method": "thread/started", "params": {"thread": {"id": "worker-thread"}}}
    )

    assert transport._thread_id == (None if resuming else "original-thread")
    assert transport._current_turn_id == "original-turn"
    assert events[-1]["type"] == "agent_event"
    assert events[-1]["agent_id"] == "worker-thread"
    transport._thread_id = "original-thread"

    await transport._handle_server_message(
        {
            "method": "turn/completed",
            "params": {"threadId": "worker-thread", "turn": {"id": "worker-turn"}},
        }
    )
    assert transport._current_turn_id == "original-turn"
    await transport._handle_server_message(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "original-thread", "delta": "Parent still working"},
        }
    )
    assert events[-1]["type"] == "content_block_delta"
    assert events[-1]["delta"]["text"] == "Parent still working"

    await transport.send_message("Continue my original task", msg_id="followup")

    assert transport._send_rpc.call_args.args[1]["threadId"] == "original-thread"
