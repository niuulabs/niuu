"""Tests for Skuld broker activity state reporting."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.broker import Broker
from skuld.config import SkuldSettings


class TestActivityStateReporting:
    """Tests for Broker._report_activity_state."""

    @pytest.fixture
    def settings(self, tmp_path):
        return SkuldSettings(
            session={"id": "test-session-123"},
            transport="subprocess",
            host="0.0.0.0",
            port=8081,
        )

    @pytest.fixture
    def test_broker(self, settings, tmp_path):
        settings.session.workspace_dir = str(tmp_path)
        b = Broker(settings=settings)
        b.volundr_api_url = "http://volundr:8000"
        return b

    def test_initial_activity_state(self, test_broker):
        """Broker should start with idle activity state."""
        assert test_broker._activity_state == "idle"

    @pytest.mark.asyncio
    async def test_report_activity_state_changes(self, test_broker):
        """Activity state should update when a new state is reported."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._http_client_jwt = None

        await test_broker._report_activity_state("active")

        assert test_broker._activity_state == "active"
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/activity" in call_args[0][0]
        assert call_args[1]["json"]["state"] == "active"

    @pytest.mark.asyncio
    async def test_report_activity_state_deduplicates(self, test_broker):
        """Reporting the same state twice should not make a second HTTP call."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._http_client_jwt = None

        await test_broker._report_activity_state("active")
        await test_broker._report_activity_state("active")

        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_report_activity_state_transitions(self, test_broker):
        """State transitions should each trigger an HTTP call."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._http_client_jwt = None

        await test_broker._report_activity_state("active")
        await test_broker._report_activity_state("tool_executing")
        await test_broker._report_activity_state("idle")

        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_report_activity_state_no_volundr_url(self, test_broker):
        """When volundr_api_url is empty, no HTTP call should be made."""
        test_broker.volundr_api_url = ""

        await test_broker._report_activity_state("active")

        assert test_broker._activity_state == "active"
        # No crash, state updated locally

    @pytest.mark.asyncio
    async def test_report_activity_state_http_error_silent(self, test_broker):
        """HTTP errors should be silently logged, not raised."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
        test_broker._http_client = mock_client
        test_broker._http_client_jwt = None

        # Should not raise
        await test_broker._report_activity_state("active")

        assert test_broker._activity_state == "active"

    @pytest.mark.asyncio
    async def test_report_activity_includes_metadata(self, test_broker):
        """Activity report should include turn_count and duration_seconds."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._http_client_jwt = None

        # Simulate some turns
        test_broker._artifacts.turn_count = 5

        await test_broker._report_activity_state("active")

        call_args = mock_client.post.call_args
        metadata = call_args[1]["json"]["metadata"]
        assert metadata["turn_count"] == 5
        assert "duration_seconds" in metadata


class TestCliEventActivityIntegration:
    """Tests for activity state changes triggered by CLI events."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "test-session-456"},
            transport="subprocess",
            host="0.0.0.0",
            port=8081,
        )
        settings.session.workspace_dir = str(tmp_path)
        b = Broker(settings=settings)
        b.volundr_api_url = "http://volundr:8000"
        return b

    @pytest.mark.asyncio
    async def test_assistant_event_triggers_active(self, test_broker):
        """An assistant event should trigger an 'active' activity report."""
        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            # Mock channels to avoid broadcast errors
            test_broker._channels = MagicMock()
            test_broker._channels.count = 0
            test_broker._channels.broadcast = AsyncMock()

            await test_broker._handle_cli_event({"type": "assistant", "message": {"content": []}})

            # Check that active was reported
            active_calls = [c for c in mock_report.call_args_list if c[0][0] == "active"]
            assert len(active_calls) >= 1

    @pytest.mark.asyncio
    async def test_result_event_triggers_idle(self, test_broker):
        """A result event should trigger an 'idle' activity report."""
        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            test_broker._channels = MagicMock()
            test_broker._channels.count = 0
            test_broker._channels.broadcast = AsyncMock()

            # Mock _report_usage to avoid HTTP call
            with patch.object(test_broker, "_report_usage", new_callable=AsyncMock):
                await test_broker._handle_cli_event({"type": "result", "result": "done"})

            idle_calls = [c for c in mock_report.call_args_list if c[0][0] == "idle"]
            assert len(idle_calls) >= 1

    @pytest.mark.asyncio
    async def test_report_activity_rides_cli_session_id(self, test_broker):
        """The transport's conversation id rides on the activity report so
        Volundr can persist it and resume the session after a restart."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._http_client_jwt = None
        transport = MagicMock()
        transport.session_id = "claude-sess-42"
        test_broker._transport = transport

        await test_broker._report_activity_state("active")

        payload = mock_client.post.call_args[1]["json"]
        assert payload["metadata"]["cli_session_id"] == "claude-sess-42"

    @pytest.mark.asyncio
    async def test_report_activity_omits_cli_session_id_when_absent(self, test_broker):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._http_client_jwt = None
        transport = MagicMock()
        transport.session_id = None
        test_broker._transport = transport

        await test_broker._report_activity_state("active")

        payload = mock_client.post.call_args[1]["json"]
        assert "cli_session_id" not in payload["metadata"]


class TestAttentionAndHeartbeat:
    """Tests for awaiting_input gating and the progress heartbeat."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "test-session-789"},
            transport="subprocess",
            host="0.0.0.0",
            port=8081,
        )
        settings.session.workspace_dir = str(tmp_path)
        b = Broker(settings=settings)
        b.volundr_api_url = "http://volundr:8000"
        return b

    @pytest.mark.asyncio
    async def test_enter_attention_reports_awaiting_input(self, test_broker):
        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            await test_broker._enter_attention(
                "askq-1", "question", prompt="Which DB?", options=[{"question": "x"}]
            )

        assert test_broker._pending_attention == {"askq-1": "question"}
        args, kwargs = mock_report.call_args
        assert args[0] == "awaiting_input"
        assert kwargs["extra_metadata"]["kind"] == "question"
        assert kwargs["extra_metadata"]["request_id"] == "askq-1"
        assert kwargs["extra_metadata"]["prompt"] == "Which DB?"

    @pytest.mark.asyncio
    async def test_exit_attention_resumes_active_when_last_gate_clears(self, test_broker):
        test_broker._activity_state = "awaiting_input"
        test_broker._pending_attention = {"askq-1": "question"}
        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            await test_broker._exit_attention("askq-1")

        assert test_broker._pending_attention == {}
        mock_report.assert_awaited_once_with("active")

    @pytest.mark.asyncio
    async def test_exit_attention_stays_blocked_with_other_gates(self, test_broker):
        test_broker._activity_state = "awaiting_input"
        test_broker._pending_attention = {"askq-1": "question", "perm-2": "permission"}
        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            await test_broker._exit_attention("askq-1")

        assert test_broker._pending_attention == {"perm-2": "permission"}
        mock_report.assert_not_called()

    @pytest.mark.asyncio
    async def test_ask_user_question_event_enters_awaiting_input(self, test_broker):
        test_broker._channels = MagicMock()
        test_broker._channels.count = 0
        test_broker._channels.broadcast = AsyncMock()

        with patch.object(test_broker, "_enter_attention", new_callable=AsyncMock) as mock_enter:
            await test_broker._handle_cli_event(
                {
                    "type": "ask_user_question",
                    "request_id": "askq-9",
                    "questions": [{"question": "Pick one"}],
                }
            )
            await asyncio.sleep(0)

        mock_enter.assert_awaited_once()
        assert mock_enter.call_args[0][0] == "askq-9"
        assert mock_enter.call_args[0][1] == "question"

    @pytest.mark.asyncio
    async def test_ask_user_answer_exits_attention(self, test_broker):
        transport = MagicMock()
        transport.send_control = AsyncMock()
        test_broker._transport = transport
        test_broker._pending_attention = {"askq-9": "question"}
        test_broker._activity_state = "awaiting_input"

        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            await test_broker._dispatch_browser_message(
                {"type": "ask_user_answer", "request_id": "askq-9", "answers": ["A"]}
            )

        assert test_broker._pending_attention == {}
        mock_report.assert_awaited_once_with("active")

    @pytest.mark.asyncio
    async def test_permission_needing_human_enters_awaiting_input(self, test_broker):
        test_broker._pending_permission_requests["perm-1"] = {
            "tool_name": "Bash",
            "description": "rm -rf /tmp/x",
        }
        with patch.object(
            test_broker,
            "_evaluate_permission_auto_approval",
            new_callable=AsyncMock,
            return_value={"can_auto_approve": False},
        ):
            with patch.object(
                test_broker, "_enter_attention", new_callable=AsyncMock
            ) as mock_enter:
                await test_broker._auto_approve_permission_request("perm-1")

        mock_enter.assert_awaited_once()
        assert mock_enter.call_args[0][0] == "perm-1"
        assert mock_enter.call_args[0][1] == "permission"

    @pytest.mark.asyncio
    async def test_permission_resolution_exits_attention(self, test_broker):
        transport = MagicMock()
        transport.send_control_response = AsyncMock()
        test_broker._transport = transport
        test_broker._channels = MagicMock()
        test_broker._channels.broadcast = AsyncMock()
        test_broker._pending_attention = {"perm-1": "permission"}
        test_broker._activity_state = "awaiting_input"

        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            await test_broker._send_permission_control_response(
                "perm-1", {"behavior": "allow"}, auto_approved=False
            )

        assert "perm-1" not in test_broker._pending_attention
        mock_report.assert_awaited_once_with("active")

    @pytest.mark.asyncio
    async def test_heartbeat_extra_bypasses_dedup(self, test_broker):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._http_client_jwt = None

        await test_broker._report_activity_state("active")
        await test_broker._report_activity_state("active", extra_metadata={"heartbeat": True})

        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_activity_extra_cached_and_cleared(self, test_broker):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._http_client_jwt = None

        await test_broker._report_activity_state(
            "awaiting_input", extra_metadata={"kind": "question", "request_id": "r1"}
        )
        assert test_broker._activity_extra == {"kind": "question", "request_id": "r1"}

        # A plain report resets the cached context (so an active heartbeat
        # doesn't carry a stale question's request_id).
        await test_broker._report_activity_state("active")
        assert test_broker._activity_extra == {}

    @pytest.mark.asyncio
    async def test_heartbeat_loop_reports_when_busy(self, test_broker):
        test_broker._settings.activity_heartbeat.interval_seconds = 0.01
        test_broker._activity_state = "tool_executing"

        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            task = asyncio.create_task(test_broker._activity_heartbeat_loop())
            await asyncio.sleep(0.03)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert mock_report.await_count >= 1
        args, kwargs = mock_report.call_args
        assert args[0] == "tool_executing"
        assert kwargs["extra_metadata"]["heartbeat"] is True

    @pytest.mark.asyncio
    async def test_heartbeat_loop_skips_idle(self, test_broker):
        test_broker._settings.activity_heartbeat.interval_seconds = 0.01
        test_broker._activity_state = "idle"

        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            task = asyncio.create_task(test_broker._activity_heartbeat_loop())
            await asyncio.sleep(0.03)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        mock_report.assert_not_called()


class TestActivityStateSinceTimestamp:
    """Server-authoritative state machine: the _set_activity_state setter stamps
    _activity_state_since only on a real change, and state_since rides the wire."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "test-session-since"},
            transport="subprocess",
            host="0.0.0.0",
            port=8081,
        )
        settings.session.workspace_dir = str(tmp_path)
        b = Broker(settings=settings)
        b.volundr_api_url = "http://volundr:8000"
        return b

    def test_initial_since_is_stamped(self, test_broker):
        """_activity_state_since is initialized alongside _activity_state."""
        assert isinstance(test_broker._activity_state_since, float)
        assert test_broker._activity_state_since > 0

    def test_set_activity_state_stamps_since_on_change(self, test_broker):
        """A real state change advances _activity_state_since."""
        before = test_broker._activity_state_since
        test_broker._activity_state_since = before - 100  # backdate so a change is visible
        test_broker._set_activity_state("active")
        assert test_broker._activity_state == "active"
        assert test_broker._activity_state_since > before - 100

    def test_set_activity_state_does_not_reset_since_on_reassert(self, test_broker):
        """Re-asserting the SAME state must NOT reset _since (elapsed stays accurate)."""
        test_broker._set_activity_state("tool_executing")
        stamped = test_broker._activity_state_since
        # Re-assert the identical state — the timestamp must be untouched.
        test_broker._set_activity_state("tool_executing")
        assert test_broker._activity_state_since == stamped

    @pytest.mark.asyncio
    async def test_report_always_includes_state_and_state_since(self, test_broker):
        """Every report carries BOTH state and state_since (never omitted, incl. idle)."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._http_client_jwt = None

        await test_broker._report_activity_state("idle")
        # idle == initial state, so force a transition first to make the report land.
        await test_broker._report_activity_state("active")

        payload = mock_client.post.call_args[1]["json"]
        assert payload["state"] == "active"
        assert "state_since" in payload
        # ISO8601 UTC string matching the surrounding datetime wire convention.
        assert payload["state_since"].endswith("+00:00")

    @pytest.mark.asyncio
    async def test_idle_transition_includes_state_since(self, test_broker):
        """An idle transition (not just busy states) carries state_since too."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._http_client_jwt = None

        await test_broker._report_activity_state("active")
        mock_client.post.reset_mock()
        await test_broker._report_activity_state("idle")

        payload = mock_client.post.call_args[1]["json"]
        assert payload["state"] == "idle"
        assert "state_since" in payload


class TestProvisioningAndStoppedStates:
    """provisioning is left for idle once the REPL is ready; transport death -> stopped."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "test-session-prov"},
            transport="subprocess",
            host="0.0.0.0",
            port=8081,
        )
        settings.session.workspace_dir = str(tmp_path)
        b = Broker(settings=settings)
        b.volundr_api_url = "http://volundr:8000"
        return b

    @pytest.mark.asyncio
    async def test_system_init_transitions_provisioning_to_idle(self, test_broker):
        """A system/init frame (REPL ready) flips provisioning -> idle."""
        test_broker._set_activity_state("provisioning")
        test_broker._channels = MagicMock()
        test_broker._channels.count = 0
        test_broker._channels.broadcast = AsyncMock()
        test_broker._transport = MagicMock()
        test_broker._transport.capabilities.slash_commands = False

        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            await test_broker._handle_cli_event({"type": "system", "subtype": "init"})
            await asyncio.sleep(0)

        idle_calls = [c for c in mock_report.call_args_list if c[0][0] == "idle"]
        assert len(idle_calls) >= 1

    @pytest.mark.asyncio
    async def test_system_init_does_not_clobber_active(self, test_broker):
        """A re-init while ACTIVE (reconnect) must NOT downgrade to idle."""
        test_broker._set_activity_state("active")
        test_broker._channels = MagicMock()
        test_broker._channels.count = 0
        test_broker._channels.broadcast = AsyncMock()
        test_broker._transport = MagicMock()
        test_broker._transport.capabilities.slash_commands = False

        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            await test_broker._handle_cli_event({"type": "system", "subtype": "init"})
            await asyncio.sleep(0)

        idle_calls = [c for c in mock_report.call_args_list if c[0][0] == "idle"]
        assert idle_calls == []

    @pytest.mark.asyncio
    async def test_transport_stopped_event_reports_stopped(self, test_broker):
        """A transport_stopped event reports the terminal 'stopped' activity state."""
        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            await test_broker._handle_cli_event(
                {"type": "transport_stopped", "reason": "tmux_session_gone"}
            )

        stopped_calls = [c for c in mock_report.call_args_list if c[0][0] == "stopped"]
        assert len(stopped_calls) == 1
        assert stopped_calls[0][1]["extra_metadata"]["reason"] == "tmux_session_gone"


class TestTurnStartActive:
    """active is reported immediately on turn START (terminal_prompt_submitted),
    not only when the first assistant token arrives."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "test-session-turnstart"},
            transport="subprocess",
            host="0.0.0.0",
            port=8081,
        )
        settings.session.workspace_dir = str(tmp_path)
        b = Broker(settings=settings)
        b.volundr_api_url = "http://volundr:8000"
        return b

    @pytest.mark.asyncio
    async def test_turn_start_reports_active(self, test_broker):
        test_broker._channels = MagicMock()
        test_broker._channels.count = 0
        test_broker._channels.broadcast = AsyncMock()

        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            await test_broker._handle_cli_event(
                {"type": "terminal_prompt_submitted", "msg_id": "m1"}
            )
            await asyncio.sleep(0)

        active_calls = [c for c in mock_report.call_args_list if c[0][0] == "active"]
        assert len(active_calls) >= 1

    @pytest.mark.asyncio
    async def test_turn_start_does_not_report_active_while_awaiting(self, test_broker):
        """The answer that unblocks a gate also arrives as a prompt-submit — it must
        NOT clobber awaiting_input before _exit_attention runs."""
        test_broker._channels = MagicMock()
        test_broker._channels.count = 0
        test_broker._channels.broadcast = AsyncMock()
        test_broker._pending_attention = {"askq-1": "question"}

        with patch.object(
            test_broker, "_report_activity_state", new_callable=AsyncMock
        ) as mock_report:
            await test_broker._handle_cli_event(
                {"type": "terminal_prompt_submitted", "msg_id": "m1"}
            )
            await asyncio.sleep(0)

        active_calls = [c for c in mock_report.call_args_list if c[0][0] == "active"]
        assert active_calls == []
