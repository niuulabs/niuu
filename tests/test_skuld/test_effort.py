"""Effort is a control: validate native levels, preserve exact values and resume them."""

import json
from unittest.mock import AsyncMock

import pytest

from bifrost.config import BifrostConfig, ManagedModelConfig
from niuu.domain.reasoning import preferred_effort, validate_effort
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.effort import effort_argument
from skuld.transports.codex_ws import CodexWebSocketTransport, _normalize_codex_effort
from skuld.transports.grok import GrokACPTransport
from skuld.transports.muse import MuseMSPTransport
from skuld.transports.pi import PiProtocolError, PiRpcTransport
from skuld.transports.tmux_interactive import TmuxInteractiveTransport


@pytest.fixture
def broker(tmp_path):
    b = Broker(
        SkuldSettings(
            session={
                "id": "effort-test",
                "model": "gpt-6-astra",
                "workspace_dir": str(tmp_path),
                "reasoning_effort": "xhigh",
            }
        )
    )
    b._conversation_history_path = lambda: tmp_path / "conversation.json"
    b._transport = CodexWebSocketTransport(
        str(tmp_path), model="gpt-6-astra", reasoning_effort="xhigh"
    )
    b._emit_broker_frame = AsyncMock()
    return b


@pytest.mark.parametrize("level", ["low", "medium", "high", "xhigh", "max", "ultra"])
async def test_codex_control_reaches_next_turn_without_clamping(tmp_path, level):
    t = CodexWebSocketTransport(str(tmp_path), model="gpt-6-astra")
    await t.send_control("set_effort", effort=level)
    assert _normalize_codex_effort(t._reasoning_effort, t._model) == level
    assert (await t.get_effort())["current"] == level


@pytest.mark.parametrize(
    "text,argument",
    [
        (" /effort ", ""),
        ("/effort extra", "extra"),
        ("/effortless x", None),
        ("explain /effort", None),
    ],
)
def test_exact_reserved_command(text, argument):
    assert effort_argument(text) == argument


async def test_control_ack_persists_only_confirmed_value_and_resumes(broker):
    result = await broker.handle_effort("extra", request_id="one")
    assert result["current"] == "xhigh"
    assert result["request_id"] == "one"
    await broker.handle_effort("max")
    assert broker._restored_effort() == "max"
    assert json.loads(broker._effort_state_path().read_text())["effort"] == "max"
    with pytest.raises(ValueError):
        await broker.handle_effort("made-up")
    assert broker._restored_effort() == "max"
    broker.model = "gpt-5.6-sol"
    assert broker._restored_effort() == "xhigh"  # model change invalidates old choice


async def test_failed_native_control_does_not_persist_or_claim_success(broker):
    broker._transport.send_control = AsyncMock(side_effect=RuntimeError("native rejected"))
    with pytest.raises(RuntimeError, match="native rejected"):
        await broker.handle_effort("low")
    assert not broker._effort_state_path().exists()
    broker._emit_broker_frame.assert_not_awaited()


async def test_plain_effort_command_never_reaches_model_prompt(broker):
    broker._transport.send_message = AsyncMock()
    broker._activate_user_turn = AsyncMock()
    broker._emit_delivery_ack = AsyncMock()
    await broker._deliver_user_message_and_ack("/effort low", request_id="one", msg_id="turn")
    broker._transport.send_message.assert_not_awaited()
    broker._emit_delivery_ack.assert_awaited_once_with("one", "turn", "delivered")
    assert broker._restored_effort() == "low"


async def test_bad_effort_command_is_a_terminal_visible_failure(broker):
    broker._fail_user_delivery = AsyncMock()
    broker._transport.send_message = AsyncMock()
    await broker._deliver_user_message_and_ack("/effort bogus", request_id="one", msg_id="turn")
    broker._transport.send_message.assert_not_awaited()
    broker._fail_user_delivery.assert_awaited_once()


async def test_structured_control_and_discovery(broker):
    await broker._dispatch_browser_message(
        {"type": "set_effort", "effort": "max", "request_id": "one"}
    )
    assert broker._restored_effort() == "max"
    commands = await broker.discover_slash_commands()
    assert [c["name"] for c in commands].count("/effort") == 1
    await broker._dispatch_browser_message(
        {"type": "slash_command", "command": "/effort", "arguments": "low"}
    )
    assert broker._restored_effort() == "low"


async def test_pi_enforces_actual_model_map_and_native_confirmation(tmp_path):
    t = PiRpcTransport(str(tmp_path), model="spark/qwen3.8-flash-next")
    state = {
        "thinkingLevel": "high",
        "model": {
            "reasoning": True,
            "thinkingLevelMap": {
                "minimal": None,
                "low": None,
                "medium": None,
                "xhigh": None,
                "max": None,
            },
        },
    }

    async def command(kind, **kwargs):
        if kind == "set_thinking_level":
            state["thinkingLevel"] = kwargs["level"]
        return dict(state)

    t._command = AsyncMock(side_effect=command)
    assert (await t.get_effort())["levels"] == ["off", "high"]
    with pytest.raises(ValueError):
        await t.send_control("set_effort", effort="xhigh")
    await t.send_control("set_effort", effort="off")
    assert (await t.get_effort())["current"] == "off"
    t._command = AsyncMock(return_value=state)
    with pytest.raises(PiProtocolError):
        await t.send_control("set_effort", effort="high")


async def test_grok_uses_discovered_option_id_and_checks_native_response(tmp_path):
    t = GrokACPTransport(str(tmp_path))
    option = {
        "id": "native_effort",
        "category": "thought_level",
        "currentValue": "high",
        "options": [{"value": "low"}, {"value": "high"}],
    }
    t._read_effort_options({"configOptions": [option]})
    t._session_id = "native-session"
    t._acp_send = AsyncMock(return_value={"configOptions": [{**option, "currentValue": "low"}]})
    await t.send_control("set_effort", effort="low")
    t._acp_send.assert_awaited_once_with(
        "session/set_config_option",
        {"sessionId": "native-session", "configId": "native_effort", "value": "low"},
    )
    with pytest.raises(ValueError):
        await t.send_control("set_effort", effort="ultra")


async def test_muse_and_claude_launch_and_change(tmp_path):
    muse = MuseMSPTransport(str(tmp_path), model="muse-spark-1.3")
    await muse.send_control("set_effort", effort="none")
    assert muse._reasoning_effort == "none"
    with pytest.raises(ValueError):
        await muse.send_control("set_effort", effort="max")
    claude = TmuxInteractiveTransport(
        str(tmp_path), model="claude-fable-5-1", reasoning_effort="xhigh"
    )
    args = claude._interactive_argv()
    assert args[args.index("--effort") + 1] == "xhigh"
    claude._wait_for_repl_ready = AsyncMock()
    claude._send_slash_command = AsyncMock()
    claude._capture_pane_text = AsyncMock(
        return_value="Set effort level to high\nSet effort level to low"
    )
    await claude._set_effort("low")
    assert (await claude.get_effort())["current"] == "low"
    claude._turn_active = True
    with pytest.raises(ValueError, match="working"):
        await claude._set_effort("high")


def test_catalog_metadata_and_host_only_additions():
    base = BifrostConfig()
    assert base.model_entry("gpt-6-astra").default_effort == "xhigh"
    assert base.model_entry("grok-4.5").effort_levels == ["low", "medium", "high"]
    local = ManagedModelConfig(
        id="spark/qwen",
        name="Qwen",
        session_definition="skuldPi",
        effort_levels=["off", "high"],
        default_effort="high",
    )
    spark = BifrostConfig(additional_models=[local])
    assert spark.model_entry("spark/qwen") is not None
    assert base.model_entry("spark/qwen") is None
    assert spark.model_entry("gpt-6-astra") is not None
    assert preferred_effort(["off", "high"]) == "high"
    assert validate_effort("extra-high", ["xhigh"]) == "xhigh"
    with pytest.raises(ValueError):
        ManagedModelConfig(id="invalid", name="invalid", effort_levels=[], default_effort="high")
