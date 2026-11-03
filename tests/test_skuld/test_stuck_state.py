"""G10 / F12: a session must not stay ``active`` after its turn ended.

Control-plane design §V8.9 (F12a–c) and §V9.2 (F12d's ``open_controls``).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.activity_reporting import is_idle_prompt_hook, is_turn_boundary_flush
from skuld.broker import Broker
from skuld.config import SkuldSettings


@pytest.fixture
def broker(tmp_path):
    settings = SkuldSettings(
        session={"id": "test-session-g10"},
        transport="subprocess",
        host="0.0.0.0",
        port=8081,
    )
    settings.session.workspace_dir = str(tmp_path)
    b = Broker(settings=settings)
    b.volundr_api_url = "http://volundr:8000"
    b._channels = MagicMock()
    b._channels.count = 0
    b._channels.broadcast = AsyncMock()
    return b


class _TurnTrackingTransport:
    """A transport that reports its own turn flag (like tmux, Codex, SDK)."""

    def __init__(self, turn_active: bool) -> None:
        self.turn_active = turn_active
        self.session_id = None
        self.capabilities = MagicMock(steering_mode="native", slash_commands=False)

    @property
    def is_turn_active(self) -> bool:
        return self.turn_active


def _transport(turn_active: bool) -> _TurnTrackingTransport:
    return _TurnTrackingTransport(turn_active)


async def _drain() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def _hook(name: str, **payload) -> dict:
    return {
        "type": "claude_hook",
        "event_type": "claude.hook",
        "hook_event_name": name,
        "payload": {"hook_event_name": name, **payload},
        "metadata": {"source": "claude_hook"},
    }


IDLE_PROMPT = _hook("Notification", notification_type="idle_prompt", prompt_id="p1")
FLUSH = {
    "type": "terminal_prompt_submitted",
    "event_type": "claude.prompt.submitted",
    "prompt": "steer",
    "msg_id": "msg-1",
    "metadata": {"source": "turn_boundary_flush", "reason": "turn_ended"},
}
REAL_PROMPT = {
    "type": "terminal_prompt_submitted",
    "event_type": "claude.prompt.submitted",
    "prompt": "hello",
    "msg_id": "msg-2",
    "metadata": {"source": "claude_hook"},
}


def test_predicates():
    assert is_turn_boundary_flush(FLUSH)
    assert not is_turn_boundary_flush(REAL_PROMPT)
    assert not is_turn_boundary_flush({"type": "terminal_prompt_submitted"})
    assert is_idle_prompt_hook(IDLE_PROMPT)
    assert not is_idle_prompt_hook(_hook("Notification", notification_type="permission_prompt"))
    assert not is_idle_prompt_hook(_hook("Stop"))
    assert not is_idle_prompt_hook({"type": "claude_hook", "hook_event_name": "Notification"})


class TestF12aFlush:
    @pytest.mark.asyncio
    async def test_flush_does_not_report_active_but_flips_the_bubble(self, broker):
        broker._transport = _transport(turn_active=False)
        with (
            patch.object(broker, "_report_activity_state", new_callable=AsyncMock) as report,
            patch.object(broker, "_activate_user_turn", new_callable=AsyncMock) as activate,
        ):
            await broker._handle_cli_event(dict(FLUSH))
            await _drain()
        report.assert_not_called()
        activate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_real_prompt_submit_still_reports_active(self, broker):
        broker._transport = _transport(turn_active=True)
        with (
            patch.object(broker, "_report_activity_state", new_callable=AsyncMock) as report,
            patch.object(broker, "_activate_user_turn", new_callable=AsyncMock) as activate,
        ):
            await broker._handle_cli_event(dict(REAL_PROMPT))
            await _drain()
        report.assert_any_call("active")
        activate.assert_awaited_once()


class TestF12bIdlePrompt:
    @pytest.mark.asyncio
    async def test_idle_prompt_heals_stale_active_when_no_turn(self, broker):
        broker._transport = _transport(turn_active=False)
        broker._activity_state = "active"
        with patch.object(broker, "_report_activity_state", new_callable=AsyncMock) as report:
            await broker._handle_cli_event(dict(IDLE_PROMPT))
            await _drain()
        report.assert_awaited_once_with("idle", extra_metadata={"healed": "idle_prompt"})

    @pytest.mark.asyncio
    async def test_idle_prompt_leaves_a_working_turn_alone(self, broker):
        broker._transport = _transport(turn_active=True)
        broker._activity_state = "tool_executing"
        broker._observe_activity_row(_hook("UserPromptSubmit"))
        broker._observe_activity_row(_hook("PreToolUse", tool_name="Bash"))
        with patch.object(broker, "_report_activity_state", new_callable=AsyncMock) as report:
            await broker._heal_on_idle_prompt()
        report.assert_not_called()

    @pytest.mark.asyncio
    async def test_dead_prompt_heals(self, broker):
        """nepal-journal: a UserPromptSubmit Claude never ran (no output, tool or Stop)."""
        broker._transport = _transport(turn_active=True)
        broker._activity_state = "active"
        broker._observe_activity_row(_hook("UserPromptSubmit"))
        broker._observe_activity_row({"type": "terminal_frame", "rows": []})
        with patch.object(broker, "_report_activity_state", new_callable=AsyncMock) as report:
            assert await broker._heal_on_idle_prompt() is True
        report.assert_awaited_once_with("idle", extra_metadata={"healed": "idle_prompt"})

    @pytest.mark.asyncio
    async def test_open_question_stays_awaiting_input(self, broker):
        broker._transport = _transport(turn_active=False)
        broker._activity_state = "awaiting_input"
        broker._pending_attention = {"q-1": "question"}
        with patch.object(broker, "_report_activity_state", new_callable=AsyncMock) as report:
            await broker._handle_cli_event(dict(IDLE_PROMPT))
            await _drain()
        report.assert_not_called()

    @pytest.mark.asyncio
    async def test_pending_gate_blocks_heal_even_if_state_is_active(self, broker):
        broker._transport = _transport(turn_active=False)
        broker._activity_state = "active"
        broker._pending_attention = {"q-1": "question"}
        with patch.object(broker, "_report_activity_state", new_callable=AsyncMock) as report:
            assert await broker._heal_on_idle_prompt() is False
        report.assert_not_called()


class TestF12cHeartbeatGuard:
    @pytest.mark.asyncio
    async def test_fresh_rows_keep_heartbeating(self, broker):
        """A 30-minute silent tool call that streams frames stays tool_executing."""
        broker._transport = _transport(turn_active=True)
        broker._observe_activity_row({"type": "terminal_frame", "rows": []})
        assert await broker._heartbeat_guard("tool_executing") is True

    @pytest.mark.asyncio
    async def test_stale_rows_without_a_turn_report_idle(self, broker):
        broker._transport = _transport(turn_active=False)
        broker._last_transport_row_at = time.monotonic() - 601
        with patch.object(broker, "_report_activity_state", new_callable=AsyncMock) as report:
            assert await broker._heartbeat_guard("active") is False
        report.assert_awaited_once_with("idle", extra_metadata={"healed": "heartbeat"})

    @pytest.mark.asyncio
    async def test_stale_rows_inside_a_turn_skip_the_heartbeat(self, broker):
        broker._transport = _transport(turn_active=True)
        broker._last_transport_row_at = time.monotonic() - 601
        with patch.object(broker, "_report_activity_state", new_callable=AsyncMock) as report:
            assert await broker._heartbeat_guard("active") is False
        report.assert_not_called()

    @pytest.mark.asyncio
    async def test_untracked_transport_is_never_guessed_idle(self, broker):
        """A transport on the port's default is_turn_active (always False) is unknown."""
        from niuu.ports.cli import CLITransport

        class Untracked(CLITransport):
            pass

        Untracked.__abstractmethods__ = frozenset()
        broker._transport = Untracked()
        broker._last_transport_row_at = time.monotonic() - 601
        assert broker._transport_tracks_turns() is False
        with patch.object(broker, "_report_activity_state", new_callable=AsyncMock) as report:
            assert await broker._heartbeat_guard("active") is False
        report.assert_not_called()
        broker._transport = None
        assert broker._transport_tracks_turns() is False

    @pytest.mark.asyncio
    async def test_awaiting_input_is_never_guarded(self, broker):
        broker._transport = _transport(turn_active=False)
        broker._last_transport_row_at = time.monotonic() - 10_000
        assert await broker._heartbeat_guard("awaiting_input") is True

    @pytest.mark.asyncio
    async def test_loop_uses_the_guard(self, broker):
        broker._settings.activity_heartbeat.interval_seconds = 0.001
        broker._transport = _transport(turn_active=False)
        broker._activity_state = "active"
        broker._last_transport_row_at = time.monotonic() - 601
        calls: list[str] = []

        async def fake_report(state, **kwargs):
            calls.append(state)
            broker._activity_state = state
            if len(calls) >= 1:
                raise asyncio.CancelledError
            return True

        with patch.object(broker, "_report_activity_state", side_effect=fake_report):
            with pytest.raises(asyncio.CancelledError):
                await broker._activity_heartbeat_loop()
        assert calls == ["idle"]

    def test_stale_output_default_is_ten_minutes(self, broker):
        assert broker._settings.activity_heartbeat.stale_output_seconds == 600.0


class TestReplayProjectModeUx:
    """Real rows (Thor session_event_log, project-mode-ux seq 10324–10332) end idle."""

    @pytest.mark.asyncio
    async def test_replay_ends_idle(self, broker):
        transport = _transport(turn_active=True)
        broker._transport = transport
        broker._activity_state = "active"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=204))
        broker._http_client = mock_client
        broker._http_client_jwt = None
        frames = [
            _hook("MessageDisplay"),
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
            _hook("Stop", last_assistant_message="done"),
            {"type": "result", "result": "done", "is_error": False, "modelUsage": {}},
        ]
        for frame in frames:
            await broker._handle_cli_event(frame)
            await _drain()
        # The transport ends its turn, then flushes the absorbed steer.
        transport.turn_active = False
        with patch.object(broker, "_activate_user_turn", new_callable=AsyncMock):
            await broker._handle_cli_event(dict(FLUSH))
            await _drain()
        assert broker._activity_state == "idle"
        await broker._handle_cli_event(dict(IDLE_PROMPT))
        await _drain()
        assert broker._activity_state == "idle"
        states = [
            call.kwargs["json"]["state"]
            for call in mock_client.post.call_args_list
            if str(call.args[0]).endswith("/activity")
        ]
        assert states[-1] == "idle"
        assert "active" not in states[states.index("idle") :]


class TestOpenControls:
    @pytest.mark.asyncio
    async def test_report_carries_open_controls(self, broker):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=204))
        broker._http_client = mock_client
        broker._http_client_jwt = None
        broker._pending_attention = {"q-1": "question", "p-2": "permission"}
        await broker._report_activity_state(
            "awaiting_input", extra_metadata={"kind": "question", "request_id": "q-1"}
        )
        controls = mock_client.post.call_args.kwargs["json"]["metadata"]["open_controls"]
        assert [(c["request_id"], c["kind"]) for c in controls] == [
            ("q-1", "question"),
            ("p-2", "permission"),
        ]
        first_opened = {c["request_id"]: c["opened_at"] for c in controls}

        # One answered: a forced refresh drops it and keeps the other's opened_at.
        await broker._exit_attention("q-1")
        assert broker._activity_state == "awaiting_input"
        controls = mock_client.post.call_args.kwargs["json"]["metadata"]["open_controls"]
        assert controls == [
            {"request_id": "p-2", "kind": "permission", "opened_at": first_opened["p-2"]}
        ]

        await broker._exit_attention("p-2")
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["state"] == "active"
        assert payload["metadata"]["open_controls"] == []

    def test_awaiting_input_without_a_request_id_is_unknown(self, broker):
        broker._pending_attention = {}
        assert broker._open_controls("awaiting_input") is None
        assert broker._open_controls("active") == []


@pytest.mark.asyncio
async def test_refresh_names_the_gate_still_open(broker):
    """Answering the gate the detail names re-points the detail at the remaining one."""
    broker._activity_state = "awaiting_input"
    broker._pending_attention = {"a": "permission", "b": "question"}
    broker._activity_extra = {"kind": "question", "request_id": "b", "prompt": "Pick"}
    with patch.object(broker, "_report_activity_state", new_callable=AsyncMock) as report:
        await broker._exit_attention("b")
    report.assert_awaited_once_with(
        "awaiting_input", extra_metadata={"kind": "permission", "request_id": "a"}, force=True
    )
