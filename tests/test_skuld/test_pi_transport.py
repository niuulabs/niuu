"""PI wire lifecycle and ordered transcript regressions (native RPC v0.85.1)."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from niuu.domain.transcript_reducer import reduce_frames
from skuld.config import PiRuntimeConfig
from skuld.transports.pi import PiProtocolError, PiRpcTransport, _text

SID = "0198f0aa-1111-7000-8000-0000000000aa"
MODEL = "openai-codex/gpt-6-astra"


@pytest.fixture
def runtime(tmp_path):
    transport = PiRpcTransport(
        str(tmp_path), model=MODEL, session_id=SID, pi_command_timeout_s=0.05, pi_turn_timeout_s=1
    )
    transport.on_event(AsyncMock())
    return transport


def captured(runtime):
    return [call.args[0] for call in runtime.event_callback.await_args_list]


def fake_process():
    process = Mock(returncode=None, stdout=asyncio.StreamReader(), stderr=asyncio.StreamReader())
    process.stdin = Mock(drain=AsyncMock())
    process.wait = AsyncMock(return_value=0)
    return process


@pytest.mark.asyncio
async def test_native_interleaving_projects_once_with_exact_bytes(runtime):
    await runtime._event({"type": "agent_start"})
    for timestamp, chunks in [(100, ["First", "\n\n- step\u2028one"]), (101, ["Done", "."])]:
        await runtime._event(
            {"type": "message_start", "message": {"role": "assistant", "timestamp": timestamp}}
        )
        for kind, value in [
            ("text_start", ""),
            *[("text_delta", c) for c in chunks],
            ("text_end", ""),
        ]:
            await runtime._event(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {"type": kind, "contentIndex": 0, "delta": value},
                }
            )
        await runtime._event(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "".join(chunks)}],
                    "usage": {"input": 10, "output": 3, "cost": {"total": 0.1}},
                    "stopReason": "stop",
                },
            }
        )
        if timestamp == 100:
            await runtime._event(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "bash-1",
                    "toolName": "bash",
                    "args": {"command": "printf 42"},
                }
            )
            await runtime._event(
                {
                    "type": "tool_execution_update",
                    "toolCallId": "bash-1",
                    "partialResult": {"content": [{"type": "text", "text": "4"}]},
                }
            )
            await runtime._event(
                {
                    "type": "tool_execution_end",
                    "toolCallId": "bash-1",
                    "result": {"content": [{"type": "text", "text": "42"}]},
                }
            )
    await runtime._event({"type": "agent_end"})
    frames = [
        SimpleNamespace(
            seq=i + 1, kind=e["type"], payload=e, request_id=None, session_id=SID, created_at=None
        )
        for i, e in enumerate(captured(runtime))
    ]
    result = reduce_frames(frames)
    parts = result.turns[0]["parts"]
    assert [p["type"] for p in parts] == ["text", "tool_use", "tool_result", "text"]
    assert parts[0]["text"] == "First\n\n- step\u2028one"
    assert parts[3]["text"] == "Done."
    assert parts[0]["id"] != parts[3]["id"]
    assert parts[2]["content"] == [{"type": "text", "text": "42"}]
    assert parts[1]["ended_at"] and parts[1]["started_at"]
    assert runtime.last_result["usage"]["input_tokens"] == 20
    assert runtime.last_result["total_cost_usd"] == 0.2
    assert len([e for e in captured(runtime) if e["type"] == "result"]) == 1


@pytest.mark.asyncio
async def test_prompt_ack_is_not_steering_consumption(runtime):
    runtime._ready = True
    runtime._process = fake_process()
    runtime._command = AsyncMock(return_value={})
    await runtime.send_control("steer", content="next", msg_id="user-1", request_id="req-1")
    assert not any(e["type"] == "user_consumed" for e in captured(runtime))
    await runtime._event(
        {
            "type": "message_start",
            "message": {"role": "user", "content": [{"type": "text", "text": "next"}]},
        }
    )
    event = captured(runtime)[0]
    assert event["type"] == "user_consumed" and event["msg_id"] == "user-1"
    assert event["request_id"] == "req-1"
    await runtime._consume({"content": "next"})
    assert len(captured(runtime)) == 1
    await runtime._event({"type": "agent_end"})
    await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,answer,expected",
    [
        ("select", {"question": "A"}, {"value": "A"}),
        ("confirm", {"question": "No"}, {"confirmed": False}),
        ("input", {"question": ["line1", "line2"]}, {"value": "line1, line2"}),
        ("editor", None, {"cancelled": True}),
    ],
)
async def test_extension_questions_round_trip(runtime, method, answer, expected):
    runtime._write = AsyncMock()
    await runtime._event(
        {
            "type": "extension_ui_request",
            "id": "q1",
            "method": method,
            "title": "Choose",
            "options": ["A", "B"],
        }
    )
    assert captured(runtime)[0]["type"] == "ask_user_question"
    await runtime.send_control("ask_user_answer", request_id="q1", answers=answer)
    runtime._write.assert_awaited_once_with(
        {"type": "extension_ui_response", "id": "q1", **expected}
    )
    with pytest.raises(PiProtocolError, match="no longer pending"):
        await runtime._answer("q1", answer)


@pytest.mark.asyncio
async def test_reader_correlates_responses_and_preserves_unicode(runtime):
    runtime._process = fake_process()
    runtime._process.stdout.feed_data(
        (
            json.dumps({"type": "system", "text": "a\u2028b\u2029c"}, ensure_ascii=False) + "\n"
        ).encode()
    )
    runtime._reader = asyncio.create_task(runtime._read_loop())
    request = asyncio.create_task(runtime._command("get_state"))
    await asyncio.sleep(0)
    command = json.loads(runtime._process.stdin.write.call_args.args[0])
    for frame in [
        {"type": "response", "id": "unrelated", "success": True},
        {"type": "response", "id": command["id"], "success": True, "data": {"ok": 42}},
    ]:
        runtime._process.stdout.feed_data((json.dumps(frame) + "\n").encode())
    assert await request == {"ok": 42}
    assert captured(runtime)[0]["pi"]["text"] == "a\u2028b\u2029c"
    assert runtime._pending == {}
    await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wire", [b"", b"[]\n", b"invalid\n", b'{"type":"message_end","message":null}\n']
)
async def test_eof_and_malformed_frames_close_active_turn(runtime, wire):
    runtime._process = fake_process()
    runtime._process.stdout.feed_data(wire)
    runtime._process.stdout.feed_eof()
    runtime._begin()
    await runtime._read_loop()
    assert not runtime.is_turn_active
    assert runtime.last_result["is_error"]
    assert runtime._turn_done.done()
    runtime._process.terminate.assert_called_once()
    await runtime.stop()


@pytest.mark.asyncio
async def test_command_timeout_and_rejection_are_explicit(runtime):
    runtime._process = fake_process()
    with pytest.raises(TimeoutError):
        await runtime._command("get_state")
    assert runtime._pending == {}
    runtime._reader = asyncio.create_task(runtime._read_loop())
    task = asyncio.create_task(runtime._command("prompt"))
    await asyncio.sleep(0)
    command = json.loads(runtime._process.stdin.write.call_args.args[0])
    runtime._process.stdout.feed_data(
        (
            json.dumps(
                {"type": "response", "id": command["id"], "success": False, "error": "No model"}
            )
            + "\n"
        ).encode()
    )
    with pytest.raises(PiProtocolError, match="No model"):
        await task
    await runtime.stop()


@pytest.mark.asyncio
async def test_turn_timeout_stops_native_process(runtime):
    runtime._process = fake_process()
    runtime._turn_timeout = 0.01
    runtime._begin()
    watchdog = runtime._watchdog
    await watchdog
    assert runtime.last_result["is_error"]
    assert "timeout" in runtime.last_result["result"]
    runtime._process.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_interrupt_clears_queue_and_aborts(runtime):
    runtime._process = fake_process()
    runtime._command = AsyncMock()
    runtime._begin()
    await runtime.interrupt()
    assert [c.args[0] for c in runtime._command.await_args_list] == ["clear_queue", "abort"]
    runtime._command.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await runtime.interrupt()
    assert runtime.last_result["is_error"]
    assert not runtime.is_turn_active


@pytest.mark.asyncio
async def test_start_resumes_exact_session_and_validates_selected_model(runtime, monkeypatch):
    process = fake_process()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    runtime._command = AsyncMock(
        return_value={
            "sessionId": SID,
            "thinkingLevel": "xhigh",
            "model": {
                "provider": "openai-codex",
                "id": "gpt-6-astra",
                "reasoning": True,
                "thinkingLevelMap": {"off": None, "xhigh": "xhigh", "max": "max"},
            },
        }
    )
    runtime._effort = "xhigh"
    runtime._agent_dir = "/configured/pi"
    runtime._name = "PI proof"
    runtime._system = "Project instructions"
    await runtime.start()
    await runtime.start()
    assert spawn.await_count == 1
    args = spawn.call_args.args
    assert args[args.index("--session-id") + 1] == SID
    assert "--no-session" not in args
    assert spawn.call_args.kwargs["env"]["PI_CODING_AGENT_DIR"] == "/configured/pi"
    assert runtime.capabilities.session_resume and runtime.capabilities.steer
    await runtime.stop()
    runtime._resume = SID
    await runtime.start()
    assert spawn.call_args.args[spawn.call_args.args.index("--session") + 1] == SID
    await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [None, {"provider": "openai-codex", "id": "wrong"}])
async def test_start_failure_surfaces_error_instead_of_idle(runtime, monkeypatch, model):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=fake_process()))
    runtime._command = AsyncMock(return_value={"sessionId": SID, "model": model})
    with pytest.raises(PiProtocolError):
        await runtime.start()
    assert runtime.last_result["is_error"]
    assert not runtime._ready


@pytest.mark.asyncio
async def test_controls_do_not_silently_accept_unsupported_actions(runtime):
    runtime._command = AsyncMock(return_value={"commands": [{"name": "review"}]})
    await runtime.send_control("set_model", model="openai-codex/gpt-5.6-sol")
    assert runtime._model == "openai-codex/gpt-5.6-sol"
    for kind, args in [("set_model", {"model": "ambiguous"}), ("set_permission_mode", {})]:
        with pytest.raises(PiProtocolError):
            await runtime.send_control(kind, **args)
    assert await runtime.discover_slash_commands() == []
    runtime._ready = True
    assert await runtime.discover_slash_commands() == [{"name": "review"}]


def test_configuration_rejects_unbounded_timeouts():
    with pytest.raises(ValueError):
        PiRuntimeConfig(turn_timeout_s=0)
    assert _text(None) == ""
    assert _text("plain") == "plain"


def test_pi_definition_and_models_use_native_rpc():
    from bifrost.config import BifrostConfig
    from niuu.config_models import default_session_definitions

    definition = default_session_definitions()["skuldPi"]
    assert definition.defaults["broker"]["transportAdapter"] == "skuld.transports.pi.PiRpcTransport"
    assert definition.default_model == MODEL
    models = [m for m in BifrostConfig().models if m.session_definition == "skuldPi"]
    assert [m.id for m in models] == [MODEL, "openai-codex/gpt-5.6-sol"]


@pytest.mark.asyncio
async def test_ios_question_answer_array_reaches_pi_as_the_answer(runtime):
    runtime._write = AsyncMock()
    await runtime._question(
        {"id": "q-ios", "method": "select", "title": "Choose", "options": ["A", "B"]}
    )
    await runtime.send_control(
        "ask_user_answer",
        request_id="q-ios",
        answers=[
            {
                "question_id": "q-ios",
                "question": "Choose",
                "answer": "B",
            }
        ],
    )
    runtime._write.assert_awaited_once_with(
        {"type": "extension_ui_response", "id": "q-ios", "value": "B"}
    )


@pytest.mark.asyncio
async def test_stopping_a_turn_resolves_stale_questions(runtime):
    runtime._begin()
    await runtime._question({"id": "q-stop", "method": "input", "title": "Input"})
    await runtime._finish("aborted")
    assert not runtime._questions
    resolved = [e for e in captured(runtime) if e["type"] == "ask_user_resolved"]
    assert resolved[0]["request_id"] == "q-stop"
    assert resolved[0]["accepted"] is False
