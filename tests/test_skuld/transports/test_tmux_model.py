"""The tmux Claude transport reports the model in effect and offers the model choices (B1d)."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.transports.tmux_interactive import SESSION_SWITCH_MODELS, TmuxInteractiveTransport
from tests.support.claude_pickers import effort_ack, effort_slider, model_ack, model_picker
from volundr.domain.session_usage import fold_usage, observation, projection_key

NATIVE = "30000000-0000-4000-8000-000000000001"
AT = datetime(2026, 10, 26, tzinfo=UTC)
USAGE = {
    "input_tokens": 3,
    "cache_read_input_tokens": 100,
    "cache_creation_input_tokens": 10,
    "output_tokens": 7,
}


def _assistant(identity: str, model: str, second: int) -> dict:
    return {
        "type": "assistant",
        "sessionId": NATIVE,
        "uuid": f"row-{identity}",
        "timestamp": (AT + timedelta(seconds=second)).isoformat(),
        "message": {"id": identity, "model": model, "usage": USAGE, "content": []},
    }


def _local_model_command(second: int) -> dict:
    # What Claude Code writes when /model is typed in the terminal.
    return {
        "type": "user",
        "sessionId": NATIVE,
        "uuid": f"cmd-{second}",
        "timestamp": (AT + timedelta(seconds=second)).isoformat(),
        "message": {
            "role": "user",
            "content": "<local-command-stdout>Set model to Opus 5.5</local-command-stdout>",
        },
    }


async def _tail(transport: TmuxInteractiveTransport, path, rows) -> list[dict]:
    with path.open("a") as stream:
        stream.writelines(json.dumps(row) + "\n" for row in rows)
    frames: list[dict] = []

    async def emit(frame):
        frames.append(frame)

    transport.on_event(emit)
    await transport.handle_claude_hook(
        {"hook_event_name": "Stop", "session_id": NATIVE, "transcript_path": str(path)}
    )
    await transport._native_usage_task
    return frames


def _project(frames: list[dict]) -> dict | None:
    state, identities = None, {}
    for seq, frame in enumerate(frames, start=1):
        event = observation(frame, seq=seq, at=AT + timedelta(seconds=seq))
        if event is None:
            continue
        key = projection_key(event, state)
        state, identities[key] = fold_usage(state, event, identities.get(key), model_windows={})
    return state


def _transport(tmp_path, **kwargs) -> TmuxInteractiveTransport:
    transport = TmuxInteractiveTransport(
        str(tmp_path), model="claude-fable-5-1", resume_session_id=NATIVE, **kwargs
    )
    transport._run_tmux = AsyncMock(side_effect=AssertionError("No TTY in this test"))
    return transport


async def test_a_model_typed_in_the_terminal_is_confirmed_by_the_next_response(tmp_path):
    transport = _transport(tmp_path)
    path = tmp_path / f"{NATIVE}.jsonl"
    frames = await _tail(transport, path, [_assistant("one", "claude-fable-5-1", 1)])
    assert (await transport.get_runtime_options())["current"]["model"] == "claude-fable-5-1"

    # /model typed in the pane: the local command row alone proves nothing...
    frames += await _tail(transport, path, [_local_model_command(2)])
    assert transport._model == "claude-fable-5-1"
    # ...the next assistant message does.
    frames += await _tail(transport, path, [_assistant("two", "claude-opus-5-5", 3)])
    options = await transport.get_runtime_options()
    assert options["current"]["model"] == "claude-opus-5-5"
    assert (await transport.get_effort())["levels"] == ["low", "medium", "high", "xhigh", "max"]
    assert _project(frames)["model"] == "claude-opus-5-5"


async def test_synthetic_rows_do_not_change_the_model(tmp_path):
    transport = _transport(tmp_path)
    path = tmp_path / f"{NATIVE}.jsonl"
    frames = await _tail(
        transport,
        path,
        [_assistant("one", "claude-opus-5-5", 1), _assistant("two", "<synthetic>", 2)],
    )
    assert transport._model == "claude-opus-5-5"
    assert _project(frames)["model"] == "claude-opus-5-5"


async def test_runtime_options_offer_the_session_switchable_models(tmp_path):
    transport = _transport(tmp_path, reasoning_effort="high")
    assert transport.capabilities.runtime_options is True
    assert transport.capabilities.set_model is True
    options = await transport.get_runtime_options(refresh=True)
    assert options["source"] == "catalog"
    assert options["applies_to"] == "next_turn"
    assert options["current"] == {
        "model": "claude-fable-5-1",
        "effort": "high",
        "service_tier": None,
    }
    assert options["active_turn"] is None
    # LEX-2022: only the /model picker's models can be chosen for this session alone.
    by_model = {entry["model"]: entry for entry in options["models"]}
    assert list(by_model) == list(SESSION_SWITCH_MODELS) == ["claude-opus-5-5", "claude-fable-5-2"]
    assert not any(entry["default"] for entry in options["models"])  # launched on fable-5-1
    assert by_model["claude-opus-5-5"]["effort_levels"] == ["low", "medium", "high", "xhigh", "max"]
    assert by_model["claude-opus-5-5"]["default_effort"] == "xhigh"
    assert by_model["claude-fable-5-2"]["default_effort"] == "high"
    transport._turn_active = True
    busy = await transport.get_runtime_options()
    assert busy["active_turn"] == {"id": None, "model": "claude-fable-5-1"}


async def test_behind_a_model_gateway_no_choices_are_offered(tmp_path):
    transport = _transport(tmp_path, model_gateway_url="http://gateway.invalid")
    assert transport.capabilities.runtime_options is False
    assert transport.capabilities.set_model is False
    with pytest.raises(RuntimeError, match="model gateway"):
        await transport.get_runtime_options()


def _driven(tmp_path, panes: list[str]) -> TmuxInteractiveTransport:
    transport = _transport(tmp_path)
    transport._alive = True
    transport._pane_poll_interval_s = 0
    transport._wait_for_repl_ready = AsyncMock()
    transport._send_slash_command = AsyncMock()
    transport._send_key = AsyncMock()
    transport._capture_pane_text = AsyncMock(side_effect=panes)
    return transport


def _keys(transport: TmuxInteractiveTransport) -> list[str]:
    return [call.args[0] for call in transport._send_key.await_args_list]


async def test_set_model_picks_this_session_only_and_waits_for_the_cli(tmp_path):
    transport = _driven(
        tmp_path,
        [model_picker(3), model_picker(2), model_ack("Opus 5.5")],
    )
    await transport.send_control("set_model", model="claude-opus-5-5")
    # Never `/model <id>`: that saves the model as the host default (LEX-2022).
    transport._send_slash_command.assert_awaited_once_with("model")
    assert _keys(transport) == ["Up", "s"]
    assert (await transport.get_runtime_options())["current"]["model"] == "claude-opus-5-5"


async def test_set_runtime_options_changes_model_then_effort(tmp_path):
    transport = _driven(
        tmp_path,
        [
            *(model_picker(3), model_picker(2), model_ack("Opus 5.5")),
            *(effort_slider("xhigh"), effort_slider("max"), effort_ack("max")),
        ],
    )
    await transport.send_control(
        "set_runtime_options", options={"model": "claude-opus-5-5", "effort": "max"}
    )
    calls = [call.args for call in transport._send_slash_command.await_args_list]
    assert calls == [("model",), ("effort",)]
    assert _keys(transport) == ["Up", "s", "Right", "s"]
    assert transport._model == "claude-opus-5-5"
    assert transport._reasoning_effort == "max"


async def test_effort_alone_needs_no_catalog_model(tmp_path):
    transport = _driven(tmp_path, [effort_slider("xhigh"), effort_slider("low"), effort_ack("low")])
    transport._model = "opus"  # a launch alias the catalog does not list
    with pytest.raises(ValueError, match="Unsupported effort"):
        await transport.send_control("set_runtime_options", options={"effort": "low"})
    transport._model = "claude-opus-5-5"
    await transport.send_control("set_runtime_options", options={"effort": "low"})
    assert transport._reasoning_effort == "low"


async def test_unchanged_model_types_nothing(tmp_path):
    transport = _driven(tmp_path, [])
    await transport.send_control("set_runtime_options", options={"model": "claude-fable-5-1"})
    transport._send_slash_command.assert_not_awaited()


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (None, "at least one"),
        ({}, "at least one"),
        ({"temperature": 1}, "Unsupported runtime option"),
        ({"service_tier": "flex"}, "no service tiers"),
        ({"model": "gpt-6-astra"}, "needs the session's model set"),
        ({"model": "claude-sonnet-5"}, "needs the session's model set"),
        ({"effort": 3}, "must be a string"),
        ({"model": "claude-opus-5-5", "effort": "ultra"}, "Unsupported effort"),
    ],
)
async def test_invalid_options_are_refused_before_typing(tmp_path, options, message):
    transport = _driven(tmp_path, [])
    with pytest.raises(ValueError, match=message):
        await transport.send_control("set_runtime_options", options=options)
    transport._send_slash_command.assert_not_awaited()


async def test_model_cannot_change_mid_turn(tmp_path):
    transport = _driven(tmp_path, [])
    transport._turn_active = True
    with pytest.raises(ValueError, match="working"):
        await transport.send_control("set_model", model="claude-opus-5-5")
    transport._send_slash_command.assert_not_awaited()


async def test_an_unacknowledged_change_raises_naming_the_knob(tmp_path):
    transport = _transport(tmp_path, effort_control_timeout_s=0.01)
    transport._alive = True
    transport._wait_for_repl_ready = AsyncMock()
    transport._send_slash_command = AsyncMock()
    transport._send_key = AsyncMock()
    transport._capture_pane_text = AsyncMock(return_value="Set model to Fable 5.1")
    with pytest.raises(RuntimeError, match="effort_control_timeout_s"):
        await transport.send_control("set_model", model="claude-opus-5-5")
    assert transport._model == "claude-fable-5-1"
    # No picker ever showed, so no Esc: a spare Esc at the prompt can open Claude's rewind.
    assert _keys(transport) == []


async def test_broker_set_model_publishes_an_applied_frame_volundr_observes(tmp_path):
    broker = Broker(
        SkuldSettings(
            session={
                "id": "model-test",
                "model": "claude-fable-5-1",
                "workspace_dir": str(tmp_path),
            }
        )
    )
    broker._conversation_history_path = lambda: tmp_path / "conversation.json"
    broker._transport = _driven(tmp_path, [model_picker(3), model_picker(2), model_ack("Opus 5.5")])
    broker._emit_broker_frame = AsyncMock()
    await broker._dispatch_browser_message(
        {"type": "set_model", "model": "claude-opus-5-5", "request_id": "app"}
    )
    frame = broker._emit_broker_frame.await_args.args[0]
    assert frame["type"] == "runtime_options"
    assert frame["applied"] is True
    assert frame["request_id"] == "app"
    assert frame["current"]["model"] == "claude-opus-5-5"
    assert broker.model == "claude-opus-5-5"
    event = observation(frame, seq=1, at=AT)
    assert (event["kind"], event["model"]) == ("model", "claude-opus-5-5")

    # A read repeats the choice; it is not a new observation.
    await broker._dispatch_browser_message({"type": "get_runtime_options"})
    read = broker._emit_broker_frame.await_args.args[0]
    assert read["applied"] is False
    assert observation(read, seq=2, at=AT) is None


async def test_a_model_switch_that_succeeded_before_its_effort_failed_is_reported(tmp_path):
    """LEX-2022: the session did switch model, so the row must learn it; then raise."""
    broker = Broker(
        SkuldSettings(
            session={
                "id": "partial-test",
                "model": "claude-fable-5-1",
                "workspace_dir": str(tmp_path),
            }
        )
    )
    broker._conversation_history_path = lambda: tmp_path / "conversation.json"
    screens = [model_picker(3), model_picker(2), model_ack("Opus 5.5")]
    broker._transport = _driven(tmp_path, [])
    # The slider then never moves: every later capture shows it at xhigh.
    broker._transport._capture_pane_text = AsyncMock(
        side_effect=lambda *_a, **_k: screens.pop(0) if screens else effort_slider("xhigh")
    )
    broker._transport._effort_control_timeout = 0.05
    broker._emit_broker_frame = AsyncMock()
    with pytest.raises(RuntimeError, match="slider did not move to max"):
        await broker.handle_runtime_options({"model": "claude-opus-5-5", "effort": "max"})
    frame = broker._emit_broker_frame.await_args.args[0]
    assert frame["applied"] is True
    assert frame["current"]["model"] == "claude-opus-5-5"
    assert frame["current"]["effort"] == "xhigh"
    assert broker.model == "claude-opus-5-5"
