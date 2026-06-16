"""Tests for Skuld broker activity state reporting."""

from __future__ import annotations

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
