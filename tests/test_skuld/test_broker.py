"""Tests for Skuld broker service."""

import asyncio
import json
import logging
import os
import time
import uuid
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import WebSocketDisconnect

from ravn.feedback import EnvironmentFeedbackRecorder
from skuld.broker import (
    Broker,
    _log_buffer,
    _SendMessageRequest,
    _TokenRedactFilter,
    app,
    broker,
    get_room_participants,
    send_message_to_session,
)
from skuld.config import SkuldSettings
from skuld.transports import (
    CodexSubprocessTransport,
    SDKTransport,
    SdkWebSocketTransport,
    SubprocessTransport,
    TransportCapabilities,
)
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry as event_registry
from sleipnir.domain.catalog import feedback_recorded
from sleipnir.testing import EventCapture


class TestBroker:
    """Tests for Broker class."""

    @pytest.fixture
    def settings(self, tmp_path):
        return SkuldSettings(
            session={"id": "test-session-123"},
            transport="subprocess",
            host="0.0.0.0",
            port=8081,
            peer_watchdog={"silence_seconds": 30.0, "tool_silence_seconds": 30.0},
        )

    @pytest.fixture
    def test_broker(self, settings, tmp_path):
        # Ensure workspace_dir points to tmp_path
        settings.session.workspace_dir = str(tmp_path)
        return Broker(settings=settings)

    def test_init_from_settings(self, test_broker, tmp_path):
        assert test_broker.session_id == "test-session-123"
        assert test_broker.workspace_dir == str(tmp_path)
        assert test_broker._transport is None

    def test_create_transport_subprocess(self, tmp_path):
        settings = SkuldSettings(
            transport="subprocess",
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, SubprocessTransport)

    def test_create_transport_sdk(self, tmp_path):
        settings = SkuldSettings(
            transport="sdk",
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, SDKTransport)

    def test_create_transport_default_is_sdk(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, SDKTransport)

    def test_create_transport_codex(self, tmp_path):
        settings = SkuldSettings(
            cli_type="codex",
            session={"id": "s1", "workspace_dir": str(tmp_path), "model": "o4-mini"},
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, CodexSubprocessTransport)

    def test_create_transport_codex_passes_model(self, tmp_path):
        settings = SkuldSettings(
            cli_type="codex",
            session={"id": "s1", "workspace_dir": str(tmp_path), "model": "gpt-4o"},
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, CodexSubprocessTransport)
        assert transport._model == "gpt-4o"

    def test_create_transport_sdk_passes_model(self, tmp_path):
        settings = SkuldSettings(
            transport="sdk",
            session={
                "id": "s1",
                "workspace_dir": str(tmp_path),
                "model": "claude-opus-4-20250514",
            },
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, SDKTransport)
        assert transport._model == "claude-opus-4-20250514"

    def test_create_transport_passes_mcp_servers(self, tmp_path):
        settings = SkuldSettings(
            transport="sdk",
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            mcp_servers=[{"name": "mimir-local", "command": "python3", "args": ["-m", "mimir"]}],
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, SDKTransport)
        assert transport._mcp_servers

    def test_create_transport_dynamic_import(self, tmp_path):
        """Dynamic transport factory uses importlib to load the configured adapter."""
        settings = SkuldSettings(
            transport_adapter="skuld.transports.subprocess.SubprocessTransport",
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings)

        with patch("skuld.broker.import_class") as mock_import:
            mock_import.return_value = SubprocessTransport
            transport = b._create_transport()

        mock_import.assert_called_once_with("skuld.transports.subprocess.SubprocessTransport")
        assert isinstance(transport, SubprocessTransport)

    def test_create_transport_sdk_adapter_path(self, tmp_path):
        settings = SkuldSettings(
            transport_adapter="skuld.transports.sdk.SDKTransport",
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, SDKTransport)

    def test_create_transport_invalid_adapter_path(self, tmp_path):
        """Invalid adapter path (no dot) raises ValueError."""
        settings = SkuldSettings(
            transport_adapter="BadPath",
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings)

        with pytest.raises(ValueError, match="must be a fully-qualified"):
            b._create_transport()

    def test_create_transport_import_error(self, tmp_path):
        """ImportError from dynamic import is wrapped in ValueError."""
        settings = SkuldSettings(
            transport_adapter="nonexistent.module.Transport",
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings)

        with patch("skuld.broker.import_class", side_effect=ImportError("no module")):
            with pytest.raises(ValueError, match="Cannot load transport adapter"):
                b._create_transport()

    @pytest.mark.asyncio
    async def test_startup_creates_workspace(self, test_broker, tmp_path):
        """Test startup creates workspace directory and initializes transport."""
        import shutil

        shutil.rmtree(tmp_path)

        await test_broker.startup()

        assert os.path.exists(test_broker.workspace_dir)
        assert test_broker._transport is not None

    @pytest.mark.asyncio
    async def test_startup_dispatches_workflow_trigger_instead_of_auto_starting_transport(
        self, tmp_path
    ):
        settings = SkuldSettings(
            session={
                "id": "wf-session-1",
                "workspace_dir": str(tmp_path),
                "initial_prompt": "Implement the requested change",
            },
            mesh={"enabled": True, "peer_id": "skuld-wf"},
            workflow_trigger={
                "enabled": True,
                "node_id": "trigger-1",
                "label": "Dispatch",
                "source": "manual dispatch",
                "event_type": "code.requested",
                "startup_delay_s": 0.0,
            },
            chronicle_watcher_enabled=False,
        )
        broker = Broker(settings=settings)
        mock_transport = AsyncMock()
        mock_transport.on_event = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.peer_id = "skuld-wf"
        mock_adapter.publish = AsyncMock()
        mock_channel = AsyncMock()
        mock_channel.channel_type = "telegram"
        mock_channel.is_open = True
        broker._channels.add(mock_channel)

        with (
            patch.object(broker, "_create_transport", return_value=mock_transport),
            patch("skuld.broker.ServiceManager") as mock_service_manager_cls,
            patch.object(
                broker,
                "_start_mesh_adapter",
                new=AsyncMock(
                    side_effect=lambda: setattr(broker, "_mesh_adapter", mock_adapter),
                ),
            ),
        ):
            mock_service_manager = AsyncMock()
            mock_service_manager_cls.return_value = mock_service_manager
            await broker.startup()
            if broker._workflow_trigger_task is not None:
                await broker._workflow_trigger_task

        mock_transport.start.assert_not_called()
        mock_adapter.publish.assert_awaited_once()
        published_event = mock_adapter.publish.await_args.args[0]
        published_topic = mock_adapter.publish.await_args.args[1]
        assert published_topic == "code.requested"
        assert published_event.payload["task_description"] == "Implement the requested change"
        assert published_event.payload["workflow_trigger_node_id"] == "trigger-1"
        assert [turn.role for turn in broker._conversation_turns] == ["user"]
        assert broker._conversation_turns[0].content == "Implement the requested change"
        assert any(
            call.args[0].get("type") == "user_confirmed"
            and call.args[0].get("content") == "Implement the requested change"
            for call in mock_channel.send_event.await_args_list
        )

    @pytest.mark.asyncio
    async def test_publish_workflow_trigger_waits_for_connected_consumers(self, tmp_path):
        settings = SkuldSettings(
            session={
                "id": "wf-session-2",
                "workspace_dir": str(tmp_path),
                "initial_prompt": "Research the topic deeply",
            },
            mesh={"enabled": True, "peer_id": "skuld-wf"},
            workflow_trigger={
                "enabled": True,
                "node_id": "trigger-1",
                "label": "Dispatch",
                "source": "manual dispatch",
                "event_type": "research.requested",
                "startup_delay_s": 0.0,
            },
            chronicle_watcher_enabled=False,
        )
        broker = Broker(settings=settings)
        broker._mesh_adapter = MagicMock(peer_id="skuld-wf", publish=AsyncMock())

        consumer = SimpleNamespace(
            peer_id="flock-research-framer",
            participant_type="ravn",
            subscribes_to=("research.requested",),
        )
        connected = False

        room_bridge = MagicMock()
        room_bridge.participants = {"flock-research-framer": consumer}
        room_bridge.is_connected.side_effect = lambda peer_id: connected
        broker._room_bridge = room_bridge

        async def connect_later() -> None:
            nonlocal connected
            await asyncio.sleep(0.02)
            connected = True

        waiter = asyncio.create_task(connect_later())
        started = time.monotonic()
        await broker._publish_workflow_trigger()
        elapsed = time.monotonic() - started
        _ = await waiter

        assert elapsed >= 0.02
        broker._mesh_adapter.publish.assert_awaited_once()
        room_bridge.is_connected.assert_called()

    @pytest.mark.asyncio
    async def test_publish_workflow_trigger_treats_mesh_consumers_as_ready(self, tmp_path):
        settings = SkuldSettings(
            session={
                "id": "wf-session-2b",
                "workspace_dir": str(tmp_path),
                "initial_prompt": "Research the topic deeply",
            },
            mesh={"enabled": True, "peer_id": "skuld-wf"},
            workflow_trigger={
                "enabled": True,
                "node_id": "trigger-1",
                "label": "Dispatch",
                "source": "manual dispatch",
                "event_type": "research.requested",
                "startup_delay_s": 0.0,
            },
            chronicle_watcher_enabled=False,
        )
        broker = Broker(settings=settings)
        broker._mesh_adapter = MagicMock(peer_id="skuld-wf", publish=AsyncMock())

        consumer = SimpleNamespace(
            peer_id="flock-research-framer",
            participant_type="ravn",
            participant_kind="mesh",
            subscribes_to=("research.requested",),
        )

        room_bridge = MagicMock()
        room_bridge.participants = {"flock-research-framer": consumer}
        room_bridge.is_connected.return_value = False
        broker._room_bridge = room_bridge

        await broker._publish_workflow_trigger()

        broker._mesh_adapter.publish.assert_awaited_once()
        room_bridge.is_connected.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_workflow_trigger_fails_when_consumers_never_connect(self, tmp_path):
        settings = SkuldSettings(
            session={
                "id": "wf-session-3",
                "workspace_dir": str(tmp_path),
                "initial_prompt": "Research the topic deeply",
            },
            mesh={"enabled": True, "peer_id": "skuld-wf"},
            workflow_trigger={
                "enabled": True,
                "node_id": "trigger-1",
                "label": "Dispatch",
                "source": "manual dispatch",
                "event_type": "research.requested",
                "startup_delay_s": 0.0,
            },
            chronicle_watcher_enabled=False,
        )
        broker = Broker(settings=settings)
        broker._mesh_adapter = MagicMock(peer_id="skuld-wf", publish=AsyncMock())

        consumer = SimpleNamespace(
            peer_id="flock-research-framer",
            participant_type="ravn",
            subscribes_to=("research.requested",),
        )

        room_bridge = MagicMock()
        room_bridge.participants = {"flock-research-framer": consumer}
        room_bridge.is_connected.return_value = False
        broker._room_bridge = room_bridge

        with patch.object(
            broker,
            "_wait_for_workflow_trigger_consumers",
            new=AsyncMock(return_value=False),
        ):
            with pytest.raises(RuntimeError, match="workflow trigger consumers"):
                await broker._publish_workflow_trigger()

        broker._mesh_adapter.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shutdown_stops_transport(self, test_broker):
        mock_transport = AsyncMock()
        test_broker._transport = mock_transport

        await test_broker.shutdown()
        mock_transport.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_closes_channels(self, test_broker):
        mock_channel = AsyncMock()
        mock_channel.channel_type = "browser"
        mock_channel.is_open = True
        test_broker._channels.add(mock_channel)

        await test_broker.shutdown()
        mock_channel.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_cli_event_forwards_to_channels(self, test_broker):
        mock_ch1 = AsyncMock()
        mock_ch1.channel_type = "browser"
        mock_ch1.is_open = True
        mock_ch2 = AsyncMock()
        mock_ch2.channel_type = "browser"
        mock_ch2.is_open = True
        test_broker._channels.add(mock_ch1)
        test_broker._channels.add(mock_ch2)

        data = {"type": "assistant", "message": {"content": "hi"}}
        await test_broker._handle_cli_event(data)

        mock_ch1.send_event.assert_called_once_with(data)
        mock_ch2.send_event.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_handle_cli_event_tracks_permission_requests(self, test_broker):
        data = {
            "type": "control_request",
            "request_id": "perm-1",
            "tool": "Bash",
            "input": {"command": "./start-dev"},
        }

        await test_broker._handle_cli_event(data)

        assert test_broker._pending_permission_requests["perm-1"] == data

    @pytest.mark.asyncio
    async def test_handle_cli_event_auto_approves_allowed_permission(self, test_broker):
        test_broker.volundr_api_url = "http://volundr.test:80"
        mock_transport = AsyncMock()
        test_broker._transport = mock_transport

        policy_check = AsyncMock(
            side_effect=[
                {"can_auto_approve": True, "delay_seconds": 0, "reason": "allowed"},
                {"can_auto_approve": True, "delay_seconds": 0, "reason": "allowed"},
            ]
        )

        with patch.object(
            test_broker,
            "_evaluate_permission_auto_approval",
            new=policy_check,
        ):
            await test_broker._handle_cli_event(
                {
                    "type": "control_request",
                    "request_id": "perm-auto",
                    "tool": "Bash",
                    "input": {"command": "./start-dev"},
                }
            )
            await asyncio.sleep(0.05)

        assert policy_check.await_count == 2
        mock_transport.send_control_response.assert_awaited_once_with(
            "perm-auto",
            {"behavior": "allow", "updatedInput": {}},
        )
        assert "perm-auto" not in test_broker._pending_permission_requests

    @pytest.mark.asyncio
    async def test_handle_cli_event_leaves_denied_permission_pending(self, test_broker):
        test_broker.volundr_api_url = "http://volundr.test:80"
        mock_transport = AsyncMock()
        test_broker._transport = mock_transport

        with patch.object(
            test_broker,
            "_evaluate_permission_auto_approval",
            new=AsyncMock(
                return_value={
                    "can_auto_approve": False,
                    "delay_seconds": 0,
                    "reason": "denylist",
                }
            ),
        ):
            await test_broker._handle_cli_event(
                {
                    "type": "control_request",
                    "request_id": "perm-denied",
                    "tool": "Bash",
                    "input": {"command": "rm -rf build"},
                }
            )
            await asyncio.sleep(0.05)

        mock_transport.send_control_response.assert_not_awaited()
        assert "perm-denied" in test_broker._pending_permission_requests

    @pytest.mark.asyncio
    async def test_handle_cli_event_reports_usage_on_result(self, test_broker):
        test_broker.volundr_api_url = "http://volundr:80"

        with patch.object(test_broker, "_report_usage", new_callable=AsyncMock) as mock_report:
            data = {"type": "result", "modelUsage": {"opus": {"inputTokens": 10}}}
            await test_broker._handle_cli_event(data)

            # _report_usage is called via create_task — let the task run
            await asyncio.sleep(0.05)

            mock_report.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_handle_cli_event_removes_broken_channels(self, test_broker):
        good_ch = AsyncMock()
        good_ch.channel_type = "browser"
        good_ch.is_open = True
        bad_ch = AsyncMock()
        bad_ch.channel_type = "browser"
        bad_ch.is_open = True
        bad_ch.send_event.side_effect = Exception("broken")
        test_broker._channels.add(good_ch)
        test_broker._channels.add(bad_ch)

        await test_broker._handle_cli_event({"type": "assistant"})

        assert bad_ch not in test_broker._channels.channels
        assert good_ch in test_broker._channels.channels

    @pytest.mark.asyncio
    async def test_handle_cli_event_broadcasts_available_commands_on_init(self, test_broker):
        mock_ch = AsyncMock()
        mock_ch.channel_type = "browser"
        mock_ch.is_open = True
        test_broker._channels.add(mock_ch)

        data = {
            "type": "system",
            "subtype": "init",
            "session_id": "s1",
            "model": "opus",
            "tools": [],
            "slash_commands": ["help", "clear"],
            "skills": ["simplify"],
        }
        await test_broker._handle_cli_event(data)

        # First call is the raw event broadcast, second is available_commands
        assert mock_ch.send_event.call_count == 2
        commands_call = mock_ch.send_event.call_args_list[1]
        sent = commands_call[0][0]
        assert sent["type"] == "available_commands"
        assert sent["slash_commands"] == ["help", "clear"]
        assert sent["skills"] == ["simplify"]

    @pytest.mark.asyncio
    async def test_handle_cli_event_skips_commands_broadcast_when_empty(self, test_broker):
        mock_ch = AsyncMock()
        mock_ch.channel_type = "browser"
        mock_ch.is_open = True
        test_broker._channels.add(mock_ch)

        data = {
            "type": "system",
            "subtype": "init",
            "session_id": "s1",
            "model": "opus",
            "tools": [],
        }
        await test_broker._handle_cli_event(data)

        # Only the raw event broadcast, no available_commands
        mock_ch.send_event.assert_called_once_with(data)

    # --- Phase 2: Permission control config pass-through ---

    def test_create_transport_sdk_passes_skip_permissions(self, tmp_path):
        settings = SkuldSettings(
            transport="sdk",
            skip_permissions=False,
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, SDKTransport)
        assert transport._skip_permissions is False

    def test_create_transport_sdk_passes_agent_teams(self, tmp_path):
        settings = SkuldSettings(
            transport="sdk",
            agent_teams=True,
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, SDKTransport)
        assert transport._agent_teams is True

    # --- Dynamic transport adapter tests ---

    def test_create_transport_explicit_adapter(self, tmp_path):
        """Direct transport_adapter bypasses legacy field resolution."""
        settings = SkuldSettings(
            transport_adapter="skuld.transports.codex.CodexSubprocessTransport",
            session={"id": "s1", "workspace_dir": str(tmp_path), "model": "o4-mini"},
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, CodexSubprocessTransport)

    def test_create_transport_invalid_module(self, tmp_path):
        """Non-existent module raises ValueError via ImportError."""
        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        settings.transport_adapter = "skuld.transports.nonexistent.FakeTransport"
        b = Broker(settings=settings)
        with pytest.raises(ValueError, match="Cannot load transport adapter"):
            b._create_transport()

    @pytest.mark.asyncio
    async def test_peer_watchdog_surfaces_silent_peer_in_chat(self, test_broker):
        test_broker._mesh_adapter = MagicMock(peer_id="skuld-peer")
        test_broker._room_bridge = MagicMock()
        test_broker._room_bridge.participants = {
            "flock-coder": MagicMock(
                display_name="coder",
                persona="coder",
                participant_type="ravn",
            )
        }
        test_broker._room_bridge.broadcast_cli_activity = AsyncMock()
        test_broker._room_bridge.broadcast_cli_message = AsyncMock()
        test_broker._report_timeline_event = AsyncMock()

        await test_broker._observe_room_peer_event(
            "flock-coder",
            "task_started",
            {"metadata": {"task_id": "task-1", "title": "Handle code.requested"}},
        )

        watch = test_broker._peer_watches["flock-coder"]
        watch.last_progress_at -= 60

        await test_broker._check_peer_watchdog_once()

        test_broker._room_bridge.broadcast_cli_activity.assert_awaited_once()
        test_broker._room_bridge.broadcast_cli_message.assert_awaited_once()
        message = test_broker._room_bridge.broadcast_cli_message.await_args.args[1]
        assert "Skuld watchdog" in message
        assert "Handle code.requested" in message
        assert "no visible progress" in message

    @pytest.mark.asyncio
    async def test_peer_watchdog_clears_watch_on_error(self, test_broker):
        test_broker._room_bridge = MagicMock()

        await test_broker._observe_room_peer_event(
            "flock-coder",
            "task_started",
            {"metadata": {"task_id": "task-1", "title": "Handle code.requested"}},
        )
        assert "flock-coder" in test_broker._peer_watches

        await test_broker._observe_room_peer_event(
            "flock-coder",
            "error",
            {"data": "backend unavailable"},
        )
        assert "flock-coder" not in test_broker._peer_watches

    @pytest.mark.asyncio
    async def test_peer_git_checkpoint_signals_increment_artifacts(self, test_broker):
        test_broker._room_bridge = MagicMock()

        await test_broker._observe_room_peer_event(
            "flock-coder",
            "task_started",
            {"metadata": {"task_id": "task-1", "title": "Handle code.requested"}},
        )
        await test_broker._observe_room_peer_event(
            "flock-coder",
            "tool_start",
            {
                "metadata": {
                    "tool_name": "BashTool",
                    "input": {"command": "git checkout -b feat/test && git push origin feat/test"},
                }
            },
        )
        await test_broker._observe_room_peer_event(
            "flock-coder",
            "tool_start",
            {
                "metadata": {
                    "tool_name": "BashTool",
                    "input": {"command": "ls proofs"},
                },
            },
        )
        await test_broker._observe_room_peer_event(
            "flock-coder",
            "tool_start",
            {
                "metadata": {
                    "tool_name": "BashTool",
                    "input": {"command": 'git add proofs && git commit -m "feat: checkpoint"'},
                }
            },
        )
        await test_broker._observe_room_peer_event(
            "flock-coder",
            "tool_result",
            {
                "metadata": {
                    "tool_name": "BashTool",
                    "is_error": False,
                },
                "data": "branch set up and pushed",
            },
        )
        await test_broker._observe_room_peer_event(
            "flock-coder",
            "tool_result",
            {
                "metadata": {
                    "tool_name": "BashTool",
                    "is_error": False,
                },
                "data": "native-workflow-step-c1.txt",
            },
        )
        await test_broker._observe_room_peer_event(
            "flock-coder",
            "tool_result",
            {
                "metadata": {
                    "tool_name": "BashTool",
                    "is_error": False,
                },
                "data": "[dev abc1234] feat: checkpoint",
            },
        )

        assert test_broker._artifacts.git_commit_count == 1
        assert test_broker._artifacts.git_push_count == 1

    @pytest.mark.asyncio
    async def test_peer_outcome_is_emitted_to_pipeline(self, test_broker):
        participant = MagicMock(persona="reviewer")
        test_broker._mesh_adapter = MagicMock(peer_id="skuld-peer")
        test_broker._room_bridge = MagicMock()
        test_broker._room_bridge.participants = {"flock-reviewer": participant}
        test_broker._emit_pipeline_event = AsyncMock()

        await test_broker._observe_room_peer_event(
            "flock-reviewer",
            "outcome",
            {
                "metadata": {"event_type": "review.completed"},
                "data": {
                    "event_type": "review.completed",
                    "verdict": "needs_changes",
                    "summary": "Needs another pass",
                    "fields": {"comments": "Fix the edge case"},
                    "room_bridge_skip": True,
                },
            },
        )

        test_broker._emit_pipeline_event.assert_awaited_once()
        args = test_broker._emit_pipeline_event.await_args.args
        assert args[0] == "outcome"
        assert args[1]["persona"] == "reviewer"
        assert args[1]["event_type"] == "review.completed"
        assert args[1]["verdict"] == "needs_changes"

    @pytest.mark.asyncio
    async def test_peer_help_needed_is_emitted_to_sleipnir(self, settings, tmp_path):
        bus = InProcessBus()
        settings.session.workspace_dir = str(tmp_path)
        broker_under_test = Broker(settings=settings, sleipnir_publisher=bus)
        broker_under_test._mesh_adapter = MagicMock(peer_id="skuld-peer")
        broker_under_test._room_bridge = MagicMock()
        broker_under_test._room_bridge.participants = {
            "flock-council-chair": MagicMock(persona="council-chair")
        }
        broker_under_test._artifacts.run_id = "run-human-1"
        broker_under_test._artifacts.saga_id = "saga-human-1"

        async with EventCapture(bus, ["ravn.help.needed"]) as capture:
            await broker_under_test._observe_room_peer_event(
                "flock-council-chair",
                "help_needed",
                {
                    "metadata": {"urgency": 0.92},
                    "data": {
                        "summary": (
                            "Need your decision on whether to prioritize latency or quality."
                        ),
                        "reason": "needs_feedback",
                        "attempted": [
                            "Compared the top two proposals",
                            "Wrote the pending decision note",
                        ],
                        "recommendation": "Pick the preferred tradeoff.",
                        "context": {
                            "slug": "research/council-human-v1",
                            "workflow_parent_event_id": "parent-1",
                        },
                        "persona": "council-chair",
                    },
                },
            )
            await bus.flush()

        assert len(capture.events) == 1
        event = capture.events[0]
        assert event.event_type == "ravn.help.needed"
        assert event.source == "ravn:flock-council-chair"
        assert event.correlation_id == "test-session-123"
        assert event.payload["session_id"] == "test-session-123"
        assert event.payload["persona"] == "council-chair"
        assert event.payload["run_id"] == "run-human-1"
        assert event.payload["saga_id"] == "saga-human-1"
        assert event.payload["reason"] == "needs_feedback"

    @pytest.mark.asyncio
    async def test_peer_help_needed_publish_failure_is_swallowed(self, settings, tmp_path):
        failing_publisher = AsyncMock()
        failing_publisher.publish.side_effect = RuntimeError("bus down")
        settings.session.workspace_dir = str(tmp_path)
        broker_under_test = Broker(settings=settings, sleipnir_publisher=failing_publisher)
        broker_under_test._mesh_adapter = MagicMock(peer_id="skuld-peer")
        broker_under_test._room_bridge = MagicMock()
        broker_under_test._room_bridge.participants = {
            "flock-council-chair": MagicMock(persona="council-chair")
        }

        await broker_under_test._observe_room_peer_event(
            "flock-council-chair",
            "help_needed",
            {"data": {"summary": "Need a human decision."}},
        )

    @pytest.mark.asyncio
    async def test_routing_only_peer_outcome_is_not_emitted_to_pipeline(self, test_broker):
        participant = MagicMock(persona="reviewer")
        test_broker._mesh_adapter = MagicMock(peer_id="skuld-peer")
        test_broker._room_bridge = MagicMock()
        test_broker._room_bridge.participants = {"flock-reviewer": participant}
        test_broker._emit_pipeline_event = AsyncMock()

        await test_broker._observe_room_peer_event(
            "flock-reviewer",
            "outcome",
            {
                "metadata": {"event_type": "review.changes_requested"},
                "data": {
                    "event_type": "review.changes_requested",
                    "canonical_event_type": "review.completed",
                    "routing_only": True,
                    "bubble_up": False,
                    "fields": {"comments": "Fix the edge case"},
                },
            },
        )

        test_broker._emit_pipeline_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_routing_only_peer_outcome_can_activate_native_workflow_gate(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-gate-1", "workspace_dir": str(tmp_path)},
            workflow={
                "graph": {
                    "nodes": [
                        {
                            "id": "spec-prd-gate",
                            "kind": "gate",
                            "label": "Approve PRD",
                            "condition": "Human approval is required before SRD drafting begins.",
                            "pendingBehavior": "help_needed",
                            "approvers": ["human"],
                        }
                    ],
                    "edges": [
                        {
                            "id": "review-to-gate",
                            "source": "spec-prd-review",
                            "target": "spec-prd-gate",
                            "label": "spec.prd.ready_for_gate -> spec.prd.ready_for_gate",
                        },
                        {
                            "id": "gate-approved",
                            "source": "spec-prd-gate",
                            "target": "spec-srd-draft",
                            "label": "spec.prd.approved -> spec.prd.approved",
                        },
                        {
                            "id": "gate-rework",
                            "source": "spec-prd-gate",
                            "target": "spec-prd-draft",
                            "label": "spec.prd.changes_requested -> spec.prd.changes_requested",
                        },
                    ],
                }
            },
        )
        broker_under_test = Broker(settings=settings)
        broker_under_test._mesh_adapter = MagicMock(peer_id="skuld-peer")
        broker_under_test._room_bridge = MagicMock()
        broker_under_test._room_bridge.participants = {
            "flock-reviewer": MagicMock(persona="reviewer")
        }
        broker_under_test._emit_pipeline_event = AsyncMock()
        broker_under_test._emit_workflow_gate_help_needed_sleipnir_event = AsyncMock()
        broker_under_test._report_activity_state = AsyncMock()

        await broker_under_test._observe_room_peer_event(
            "flock-reviewer",
            "outcome",
            {
                "metadata": {"event_type": "spec.prd.ready_for_gate"},
                "data": {
                    "event_type": "spec.prd.ready_for_gate",
                    "canonical_event_type": "spec.prd.review.completed",
                    "routing_only": True,
                    "bubble_up": False,
                    "summary": "PRD is ready for approval.",
                    "fields": {
                        "verdict": "ready_for_gate",
                        "summary": "PRD is ready for approval.",
                    },
                },
            },
        )

        gates = broker_under_test.list_workflow_gates()
        assert len(gates) == 1
        assert gates[0]["node_id"] == "spec-prd-gate"
        assert gates[0]["status"] == "pending"
        assert gates[0]["pending_behavior"] == "help_needed"
        assert gates[0]["triggered_by_event_type"] == "spec.prd.ready_for_gate"
        broker_under_test._emit_workflow_gate_help_needed_sleipnir_event.assert_awaited_once()
        broker_under_test._report_activity_state.assert_awaited_once()
        pending_events = [
            call.args
            for call in broker_under_test._emit_pipeline_event.await_args_list
            if call.args and call.args[0] == "workflow.gate.pending"
        ]
        assert len(pending_events) == 1

    @pytest.mark.asyncio
    async def test_flock_completion_accepts_stage_finisher_persona(self, test_broker):
        participant = MagicMock(persona="coordinator-finisher")
        test_broker._room_bridge = MagicMock()
        test_broker._room_bridge.participants = {"flock-coordinator-finisher": participant}
        test_broker._report_activity_state = AsyncMock()
        test_broker._flock_completion_reported = False
        test_broker._is_room_only_workflow_session = MagicMock(return_value=True)

        await test_broker._observe_room_peer_event(
            "flock-coordinator-finisher",
            "outcome",
            {
                "metadata": {"event_type": "ravn.task.completed"},
                "data": {
                    "event_type": "ravn.task.completed",
                    "fields": {
                        "verdict": "approve",
                        "summary": "done",
                        "files_changed": ["proofs/step.txt"],
                    },
                    "valid": True,
                },
            },
        )

        test_broker._report_activity_state.assert_awaited_once()
        args = test_broker._report_activity_state.await_args.args
        kwargs = test_broker._report_activity_state.await_args.kwargs
        assert args[0] == "idle"
        assert kwargs["extra_metadata"]["completion_persona"] == "coordinator-finisher"
        assert kwargs["extra_metadata"]["structured_outcome"]["verdict"] == "approve"

    @pytest.mark.asyncio
    async def test_parallel_terminal_node_emits_completion_without_finisher_persona(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-1", "workspace_dir": str(tmp_path)},
            room={"enabled": True},
            workflow={
                "graph": {
                    "nodes": [
                        {
                            "id": "run-complete",
                            "kind": "end",
                            "joinMode": "all",
                            "completionEvent": "ravn.task.completed",
                        }
                    ],
                    "edges": [
                        {
                            "id": "e-review",
                            "source": "review-stage",
                            "target": "run-complete",
                            "label": "review.completed -> review.completed",
                        },
                        {
                            "id": "e-security",
                            "source": "security-stage",
                            "target": "run-complete",
                            "label": "security.completed -> security.completed",
                        },
                    ],
                }
            },
        )
        broker_under_test = Broker(settings=settings)
        broker_under_test._mesh_adapter = MagicMock(peer_id="skuld-peer")
        broker_under_test._room_bridge = MagicMock()
        broker_under_test._room_bridge.participants = {
            "flock-reviewer": MagicMock(persona="reviewer"),
            "flock-security": MagicMock(persona="security-auditor"),
        }
        broker_under_test._artifacts.git_commit_count = 1
        broker_under_test._artifacts.git_push_count = 1
        broker_under_test._emit_pipeline_event = AsyncMock()
        broker_under_test._report_activity_state = AsyncMock()
        broker_under_test._is_room_only_workflow_session = MagicMock(return_value=True)

        await broker_under_test._observe_room_peer_event(
            "flock-reviewer",
            "outcome",
            {
                "metadata": {"event_type": "review.completed", "task_id": "review-task-1"},
                "data": {
                    "event_type": "review.completed",
                    "workflow_parent_event_id": "code-task-1",
                    "verdict": "pass",
                    "summary": "Looks good",
                    "fields": {"verdict": "pass", "summary": "Looks good"},
                    "room_bridge_skip": True,
                },
            },
        )

        broker_under_test._report_activity_state.assert_not_awaited()

        await broker_under_test._observe_room_peer_event(
            "flock-security",
            "outcome",
            {
                "metadata": {"event_type": "security.completed", "task_id": "security-task-1"},
                "data": {
                    "event_type": "security.completed",
                    "workflow_parent_event_id": "code-task-1",
                    "verdict": "pass",
                    "summary": "No security issues",
                    "fields": {"verdict": "pass", "summary": "No security issues"},
                    "room_bridge_skip": True,
                },
            },
        )

        assert broker_under_test._emit_pipeline_event.await_count == 3
        final_call = broker_under_test._emit_pipeline_event.await_args_list[-1]
        assert final_call.args[0] == "outcome"
        assert final_call.args[1]["event_type"] == "ravn.task.completed"
        assert final_call.args[1]["verdict"] == "approve"
        broker_under_test._report_activity_state.assert_awaited_once()
        completion = broker_under_test._report_activity_state.await_args.kwargs["extra_metadata"]
        assert completion["structured_outcome"]["verdict"] == "approve"

    @pytest.mark.asyncio
    async def test_parallel_terminal_node_reports_custom_completion_event_for_workflow_stop(
        self, tmp_path
    ):
        settings = SkuldSettings(
            session={"id": "sess-1", "workspace_dir": str(tmp_path)},
            room={"enabled": True},
            workflow={
                "graph": {
                    "nodes": [
                        {
                            "id": "delivery-complete",
                            "kind": "end",
                            "joinMode": "all",
                            "completionEvent": "delivery.completed",
                        }
                    ],
                    "edges": [
                        {
                            "id": "e-publish",
                            "source": "publish-stage",
                            "target": "delivery-complete",
                            "label": "delivery.completed -> delivery.completed",
                        },
                    ],
                }
            },
        )
        broker_under_test = Broker(settings=settings)
        broker_under_test._mesh_adapter = MagicMock(peer_id="skuld-peer")
        broker_under_test._room_bridge = MagicMock()
        broker_under_test._room_bridge.participants = {
            "flock-publisher": MagicMock(persona="publisher"),
        }
        broker_under_test._emit_pipeline_event = AsyncMock()
        broker_under_test._report_activity_state = AsyncMock()
        broker_under_test._is_room_only_workflow_session = MagicMock(return_value=True)

        await broker_under_test._observe_room_peer_event(
            "flock-publisher",
            "outcome",
            {
                "metadata": {"event_type": "delivery.completed", "task_id": "publish-task-1"},
                "data": {
                    "event_type": "delivery.completed",
                    "workflow_parent_event_id": "close-task-1",
                    "verdict": "published",
                    "summary": "Delivery artifacts published",
                    "fields": {"verdict": "published", "summary": "Delivery artifacts published"},
                    "room_bridge_skip": True,
                },
            },
        )

        broker_under_test._report_activity_state.assert_awaited_once()
        completion = broker_under_test._report_activity_state.await_args.kwargs["extra_metadata"]
        assert completion["completion_event_type"] == "delivery.completed"
        assert completion["completion_peer_id"] == "workflow-stop:delivery-complete"
        assert completion["structured_outcome"]["verdict"] == "approve"

    @pytest.mark.asyncio
    async def test_parallel_terminal_node_waits_for_git_push_when_required(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-1", "workspace_dir": str(tmp_path)},
            room={"enabled": True},
            workflow={
                "graph": {
                    "nodes": [
                        {
                            "id": "run-complete",
                            "kind": "end",
                            "joinMode": "all",
                            "completionEvent": "ravn.task.completed",
                            "completionRules": {
                                "requireGitCommit": True,
                                "requireGitPush": True,
                            },
                        }
                    ],
                    "edges": [
                        {
                            "id": "e-review",
                            "source": "review-stage",
                            "target": "run-complete",
                            "label": "review.completed -> review.completed",
                        },
                        {
                            "id": "e-security",
                            "source": "security-stage",
                            "target": "run-complete",
                            "label": "security.completed -> security.completed",
                        },
                    ],
                }
            },
        )
        broker_under_test = Broker(settings=settings)
        broker_under_test._mesh_adapter = MagicMock(peer_id="skuld-peer")
        broker_under_test._room_bridge = MagicMock()
        broker_under_test._room_bridge.participants = {
            "flock-reviewer": MagicMock(persona="reviewer"),
            "flock-security": MagicMock(persona="security-auditor"),
        }
        broker_under_test._artifacts.git_commit_count = 1
        broker_under_test._artifacts.git_push_count = 0
        broker_under_test._emit_pipeline_event = AsyncMock()
        broker_under_test._report_activity_state = AsyncMock()
        broker_under_test._is_room_only_workflow_session = MagicMock(return_value=True)

        await broker_under_test._observe_room_peer_event(
            "flock-reviewer",
            "outcome",
            {
                "metadata": {"event_type": "review.completed", "task_id": "review-task-1"},
                "data": {
                    "event_type": "review.completed",
                    "workflow_parent_event_id": "code-task-1",
                    "verdict": "pass",
                    "summary": "Looks good",
                    "fields": {"verdict": "pass", "summary": "Looks good"},
                    "room_bridge_skip": True,
                },
            },
        )
        await broker_under_test._observe_room_peer_event(
            "flock-security",
            "outcome",
            {
                "metadata": {"event_type": "security.completed", "task_id": "security-task-1"},
                "data": {
                    "event_type": "security.completed",
                    "workflow_parent_event_id": "code-task-1",
                    "verdict": "pass",
                    "summary": "No security issues",
                    "fields": {"verdict": "pass", "summary": "No security issues"},
                    "room_bridge_skip": True,
                },
            },
        )

        emitted_event_types = [
            call.args[1]["event_type"]
            for call in broker_under_test._emit_pipeline_event.await_args_list
        ]
        assert "ravn.task.completed" not in emitted_event_types
        broker_under_test._report_activity_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_parallel_terminal_node_accepts_repo_git_state_when_tool_counts_are_zero(
        self, tmp_path
    ):
        settings = SkuldSettings(
            session={"id": "sess-1", "workspace_dir": str(tmp_path)},
            room={"enabled": True},
            workflow={
                "graph": {
                    "nodes": [
                        {
                            "id": "run-complete",
                            "kind": "end",
                            "joinMode": "all",
                            "completionEvent": "ravn.task.completed",
                            "completionRules": {
                                "requireGitCommit": True,
                                "requireGitPush": True,
                            },
                        }
                    ],
                    "edges": [
                        {
                            "id": "e-review",
                            "source": "review-stage",
                            "target": "run-complete",
                            "label": "review.completed -> review.completed",
                        },
                        {
                            "id": "e-security",
                            "source": "security-stage",
                            "target": "run-complete",
                            "label": "security.completed -> security.completed",
                        },
                    ],
                }
            },
        )
        broker_under_test = Broker(settings=settings)
        broker_under_test._mesh_adapter = MagicMock(peer_id="skuld-peer")
        broker_under_test._room_bridge = MagicMock()
        broker_under_test._room_bridge.participants = {
            "flock-reviewer": MagicMock(persona="reviewer"),
            "flock-security": MagicMock(persona="security-auditor"),
        }
        broker_under_test._artifacts.git_commit_count = 0
        broker_under_test._artifacts.git_push_count = 0
        broker_under_test._git_workspace_checkpoint = MagicMock()
        broker_under_test._emit_pipeline_event = AsyncMock()
        broker_under_test._report_activity_state = AsyncMock()
        broker_under_test._is_room_only_workflow_session = MagicMock(return_value=True)

        with patch("skuld.broker._git_workspace_checkpoint_status", return_value=(True, True)):
            await broker_under_test._observe_room_peer_event(
                "flock-reviewer",
                "outcome",
                {
                    "metadata": {"event_type": "review.completed", "task_id": "review-task-1"},
                    "data": {
                        "event_type": "review.completed",
                        "workflow_parent_event_id": "code-task-1",
                        "verdict": "pass",
                        "summary": "Looks good",
                        "fields": {"verdict": "pass", "summary": "Looks good"},
                        "room_bridge_skip": True,
                    },
                },
            )
            await broker_under_test._observe_room_peer_event(
                "flock-security",
                "outcome",
                {
                    "metadata": {
                        "event_type": "security.completed",
                        "task_id": "security-task-1",
                    },
                    "data": {
                        "event_type": "security.completed",
                        "workflow_parent_event_id": "code-task-1",
                        "verdict": "pass",
                        "summary": "No security issues",
                        "fields": {"verdict": "pass", "summary": "No security issues"},
                        "room_bridge_skip": True,
                    },
                },
            )

        emitted_event_types = [
            call.args[1]["event_type"]
            for call in broker_under_test._emit_pipeline_event.await_args_list
        ]
        assert "ravn.task.completed" in emitted_event_types
        assert broker_under_test._artifacts.git_commit_count == 1
        assert broker_under_test._artifacts.git_push_count == 1

    def test_create_transport_invalid_class(self, tmp_path):
        """Valid module but missing class raises ValueError via AttributeError."""
        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        settings.transport_adapter = "skuld.transports.codex.NonexistentTransport"
        b = Broker(settings=settings)
        with pytest.raises(ValueError, match="Cannot load transport adapter"):
            b._create_transport()

    def test_create_transport_invalid_path_no_dot(self, tmp_path):
        """Adapter path without a dot raises ValueError."""
        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        settings.transport_adapter = "NotAFullyQualifiedPath"
        b = Broker(settings=settings)
        with pytest.raises(ValueError, match="must be a fully-qualified class path"):
            b._create_transport()

    def test_build_transport_kwargs(self, tmp_path):
        """_build_transport_kwargs returns expected superset of settings."""
        settings = SkuldSettings(
            session={
                "id": "s1",
                "workspace_dir": str(tmp_path),
                "model": "opus",
                "system_prompt": "be helpful",
                "initial_prompt": "hello",
            },
            port=9999,
            skip_permissions=False,
            approval_policy="untrusted",
            sandbox="workspace-write",
            agent_teams=True,
        )
        b = Broker(settings=settings)
        kwargs = b._build_transport_kwargs()
        assert kwargs["workspace_dir"] == str(tmp_path)
        assert kwargs["model"] == "opus"
        assert kwargs["sdk_port"] == 9999
        assert kwargs["session_id"] == "s1"
        assert kwargs["skip_permissions"] is False
        assert kwargs["approval_policy"] == "untrusted"
        assert kwargs["sandbox"] == "workspace-write"
        assert kwargs["agent_teams"] is True
        assert kwargs["system_prompt"] == "be helpful"
        assert kwargs["initial_prompt"] == "hello"

    def test_build_transport_kwargs_omits_initial_prompt_for_workflow_trigger(self, tmp_path):
        settings = SkuldSettings(
            session={
                "id": "s1",
                "workspace_dir": str(tmp_path),
                "initial_prompt": "dispatch this task",
            },
            workflow_trigger={
                "enabled": True,
                "node_id": "trigger-1",
                "label": "Dispatch",
                "source": "manual dispatch",
                "event_type": "code.requested",
            },
        )
        b = Broker(settings=settings)

        kwargs = b._build_transport_kwargs()

        assert kwargs["initial_prompt"] == ""

    def test_create_transport_filters_kwargs(self, tmp_path):
        """Only kwargs matching the constructor signature are passed."""
        settings = SkuldSettings(
            transport="subprocess",
            session={"id": "s1", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings)
        transport = b._create_transport()
        assert isinstance(transport, SubprocessTransport)
        assert transport.workspace_dir == str(tmp_path)


class TestDispatchBrowserMessage:
    """Tests for Broker._dispatch_browser_message (Phase 2/3/4)."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "test-session", "workspace_dir": str(tmp_path)},
            transport="sdk",
            skip_permissions=False,
        )
        b = Broker(settings=settings)
        b._transport = AsyncMock()
        b._transport.capabilities = TransportCapabilities()
        b._transport.is_turn_active = False
        return b

    @pytest.mark.asyncio
    async def test_dispatch_user_message(self, test_broker):
        test_broker._channels.broadcast = AsyncMock()

        await test_broker._dispatch_browser_message({"content": "hello", "request_id": "req-1"})
        # send_message now runs in a background task so the WS handler
        # doesn't block on the per-turn lock; pump the loop so the task runs.
        await asyncio.sleep(0)
        test_broker._transport.send_message.assert_called_once_with("hello")
        test_broker._channels.broadcast.assert_awaited_once_with(
            {
                "type": "user_confirmed",
                "id": ANY,
                "content": "hello",
                "request_id": "req-1",
            }
        )

    @pytest.mark.asyncio
    async def test_dispatch_structured_user_message_normalizes_attachments(self, test_broker):
        test_broker._channels.broadcast = AsyncMock()
        image_data = "a" * 200000

        await test_broker._dispatch_browser_message(
            {
                "content": [
                    {"type": "text", "text": "Please review this screenshot"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_data,
                        },
                    },
                ]
            }
        )
        await asyncio.sleep(0)

        expected = (
            "Please review this screenshot\n\n"
            "[User attached 1 image attachment. This transport forwards text only.]"
        )
        test_broker._transport.send_message.assert_called_once_with(expected)
        test_broker._channels.broadcast.assert_awaited_once_with(
            {
                "type": "user_confirmed",
                "id": ANY,
                "content": expected,
                "request_id": None,
            }
        )

    @pytest.mark.asyncio
    async def test_dispatch_attachment_only_message_uses_summary_text(self, test_broker):
        test_broker._channels.broadcast = AsyncMock()

        await test_broker._dispatch_browser_message(
            {
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "a" * 200000,
                        },
                    }
                ]
            }
        )
        await asyncio.sleep(0)

        expected = "[User attached 1 image attachment. This transport forwards text only.]"
        test_broker._transport.send_message.assert_called_once_with(expected)
        test_broker._channels.broadcast.assert_awaited_once_with(
            {
                "type": "user_confirmed",
                "id": ANY,
                "content": expected,
                "request_id": None,
            }
        )

    @pytest.mark.asyncio
    async def test_dispatch_user_message_steers_when_turn_active(self, test_broker):
        test_broker._transport.capabilities = TransportCapabilities(steer=True)
        test_broker._transport.is_turn_active = True

        await test_broker._dispatch_browser_message({"content": "Prefer option B"})
        await asyncio.sleep(0)

        test_broker._transport.send_message.assert_not_called()
        test_broker._transport.send_control.assert_called_once_with(
            "redirect",
            content="Prefer option B",
        )

    @pytest.mark.asyncio
    async def test_dispatch_user_message_steers_active_turn(self, test_broker):
        test_broker._transport.capabilities = TransportCapabilities(steer=True)
        test_broker._transport.is_turn_active = True

        await test_broker._dispatch_browser_message({"content": "change direction"})
        await asyncio.sleep(0)

        test_broker._transport.send_control.assert_called_once_with(
            "redirect",
            content="change direction",
        )
        test_broker._transport.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_structured_user_message_redirects_with_normalized_text(
        self, test_broker
    ):
        test_broker._transport.capabilities = TransportCapabilities(steer=True)
        test_broker._transport.is_turn_active = True

        await test_broker._dispatch_browser_message(
            {
                "content": [
                    {"type": "text", "text": "Switch to the new screenshot"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "b" * 200000,
                        },
                    },
                ]
            }
        )
        await asyncio.sleep(0)

        test_broker._transport.send_control.assert_called_once_with(
            "redirect",
            content=(
                "Switch to the new screenshot\n\n"
                "[User attached 1 image attachment. This transport forwards text only.]"
            ),
        )
        test_broker._transport.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_explicit_steer_active_turn_message(self, test_broker):
        test_broker._transport.capabilities = TransportCapabilities(steer=True)
        await test_broker._dispatch_browser_message(
            {"type": "steer_active_turn", "content": "abort the search"}
        )

        test_broker._transport.send_control.assert_awaited_once_with(
            "steer",
            content="abort the search",
        )

    @pytest.mark.asyncio
    async def test_dispatch_user_message_empty_ignored(self, test_broker):
        await test_broker._dispatch_browser_message({"content": ""})
        test_broker._transport.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_user_message_rejected_for_workflow_room_session(self, tmp_path):
        settings = SkuldSettings(
            session={
                "id": "test-session",
                "workspace_dir": str(tmp_path),
                "initial_prompt": "Do the work",
            },
            transport="sdk",
            room={"enabled": True},
            workflow_trigger={
                "enabled": True,
                "node_id": "trigger-1",
                "label": "Dispatch",
                "source": "manual dispatch",
                "event_type": "code.requested",
            },
        )
        broker = Broker(settings=settings)
        broker._transport = AsyncMock()
        sender_ws = AsyncMock()

        await broker._dispatch_browser_message({"content": "hello"}, sender_ws=sender_ws)

        broker._transport.send_message.assert_not_called()
        sender_ws.send_json.assert_called_once()
        sent = sender_ws.send_json.call_args[0][0]
        assert sent["type"] == "error"
        assert "Target a mesh peer instead" in sent["content"]

    @pytest.mark.asyncio
    async def test_dispatch_permission_response_allow(self, test_broker):
        await test_broker._dispatch_browser_message(
            {
                "type": "permission_response",
                "request_id": "req-999",
                "behavior": "allow",
                "updated_input": {"command": "ls -la"},
            }
        )
        test_broker._transport.send_control_response.assert_called_once_with(
            "req-999",
            {
                "behavior": "allow",
                "updatedInput": {"command": "ls -la"},
            },
        )

    @pytest.mark.asyncio
    async def test_safe_transport_control_broadcasts_background_errors(self, test_broker):
        bad_transport = AsyncMock()
        bad_transport.send_control.side_effect = RuntimeError("boom")
        test_broker._channels = AsyncMock()

        await test_broker._safe_transport_control(
            bad_transport,
            "steer",
            content="reroute the task",
        )

        test_broker._channels.broadcast.assert_awaited_once()
        payload = test_broker._channels.broadcast.await_args.args[0]
        assert payload["type"] == "error"
        assert "boom" in payload["content"]

    @pytest.mark.asyncio
    async def test_safe_transport_control_swallows_broadcast_failures(self, test_broker):
        bad_transport = AsyncMock()
        bad_transport.send_control.side_effect = RuntimeError("boom")
        test_broker._channels = AsyncMock()
        test_broker._channels.broadcast.side_effect = RuntimeError("offline")

        await test_broker._safe_transport_control(
            bad_transport,
            "steer",
            content="reroute the task",
        )

    @pytest.mark.asyncio
    async def test_dispatch_permission_response_deny(self, test_broker):
        await test_broker._dispatch_browser_message(
            {
                "type": "permission_response",
                "request_id": "req-888",
                "behavior": "deny",
            }
        )
        test_broker._transport.send_control_response.assert_called_once()
        args = test_broker._transport.send_control_response.call_args
        assert args[0][0] == "req-888"
        assert args[0][1]["behavior"] == "deny"

    @pytest.mark.asyncio
    async def test_dispatch_permission_response_with_updated_permissions(self, test_broker):
        await test_broker._dispatch_browser_message(
            {
                "type": "permission_response",
                "request_id": "req-777",
                "behavior": "allow",
                "updated_input": {"command": "ls"},
                "updated_permissions": [{"tool": "Bash", "behavior": "allow"}],
            }
        )
        args = test_broker._transport.send_control_response.call_args
        response = args[0][1]
        assert "updatedPermissions" in response
        assert response["updatedPermissions"] == [{"tool": "Bash", "behavior": "allow"}]

    @pytest.mark.asyncio
    async def test_dispatch_interrupt(self, test_broker):
        test_broker._transport.capabilities = TransportCapabilities(interrupt=True)
        await test_broker._dispatch_browser_message({"type": "interrupt"})
        test_broker._transport.send_control.assert_called_once_with("interrupt")

    @pytest.mark.asyncio
    async def test_dispatch_steer_active_turn(self, test_broker):
        test_broker._transport.capabilities = TransportCapabilities(steer=True)

        await test_broker._dispatch_browser_message(
            {"type": "steer_active_turn", "content": "Change approach"}
        )

        test_broker._transport.send_control.assert_called_once_with(
            "steer",
            content="Change approach",
        )

    @pytest.mark.asyncio
    async def test_dispatch_set_model(self, test_broker):
        test_broker._transport.capabilities = TransportCapabilities(set_model=True)
        await test_broker._dispatch_browser_message(
            {
                "type": "set_model",
                "model": "claude-opus-4-6",
            }
        )
        test_broker._transport.send_control.assert_called_once_with(
            "set_model",
            model="claude-opus-4-6",
        )

    @pytest.mark.asyncio
    async def test_dispatch_set_model_empty_ignored(self, test_broker):
        await test_broker._dispatch_browser_message(
            {
                "type": "set_model",
                "model": "",
            }
        )
        test_broker._transport.send_control.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_set_max_thinking_tokens(self, test_broker):
        test_broker._transport.capabilities = TransportCapabilities(set_thinking_tokens=True)
        await test_broker._dispatch_browser_message(
            {
                "type": "set_max_thinking_tokens",
                "max_thinking_tokens": 4096,
            }
        )
        test_broker._transport.send_control.assert_called_once_with(
            "set_max_thinking_tokens",
            max_thinking_tokens=4096,
        )

    @pytest.mark.asyncio
    async def test_dispatch_set_permission_mode(self, test_broker):
        test_broker._transport.capabilities = TransportCapabilities(set_permission_mode=True)
        await test_broker._dispatch_browser_message(
            {
                "type": "set_permission_mode",
                "mode": "bypassPermissions",
            }
        )
        test_broker._transport.send_control.assert_called_once_with(
            "set_permission_mode",
            permissionMode="bypassPermissions",
        )

    @pytest.mark.asyncio
    async def test_dispatch_rewind_files(self, test_broker):
        test_broker._transport.capabilities = TransportCapabilities(rewind_files=True)
        await test_broker._dispatch_browser_message({"type": "rewind_files"})
        test_broker._transport.send_control.assert_called_once_with("rewind_files")

    @pytest.mark.asyncio
    async def test_dispatch_mcp_set_servers(self, test_broker):
        test_broker._transport.capabilities = TransportCapabilities(mcp_set_servers=True)
        servers = [{"name": "my-mcp", "command": "node", "args": ["server.js"]}]
        await test_broker._dispatch_browser_message(
            {
                "type": "mcp_set_servers",
                "servers": servers,
            }
        )
        test_broker._transport.send_control.assert_called_once_with(
            "mcp_set_servers",
            servers=servers,
        )

    @pytest.mark.asyncio
    async def test_dispatch_no_transport_noop(self, test_broker):
        test_broker._transport = None
        # Should not raise
        await test_broker._dispatch_browser_message({"content": "hello"})

    @pytest.mark.asyncio
    async def test_dispatch_guard_blocks_unsupported_control(self, test_broker):
        """Unsupported control messages are rejected with an error to sender_ws."""
        test_broker._transport.capabilities = TransportCapabilities()  # all False
        sender_ws = AsyncMock()

        await test_broker._dispatch_browser_message({"type": "interrupt"}, sender_ws=sender_ws)

        # Control should NOT be forwarded
        test_broker._transport.send_control.assert_not_called()
        # Error should be sent back to the sender
        sender_ws.send_json.assert_called_once()
        sent = sender_ws.send_json.call_args[0][0]
        assert sent["type"] == "error"
        assert "interrupt" in sent["content"]
        assert "not supported" in sent["content"]

    @pytest.mark.asyncio
    async def test_dispatch_guard_blocks_all_guarded_controls(self, test_broker):
        """All guarded control types are blocked when capabilities are False."""
        test_broker._transport.capabilities = TransportCapabilities()  # all False
        guarded = [
            "interrupt",
            "steer_active_turn",
            "set_model",
            "set_max_thinking_tokens",
            "set_permission_mode",
            "rewind_files",
            "mcp_set_servers",
            "terminal_input",
            "terminal_key",
            "terminal_resize",
            "slash_command",
            "discover_slash_commands",
        ]
        for msg_type in guarded:
            sender_ws = AsyncMock()
            await test_broker._dispatch_browser_message({"type": msg_type}, sender_ws=sender_ws)
            sender_ws.send_json.assert_called_once()
            sent = sender_ws.send_json.call_args[0][0]
            assert sent["type"] == "error"
            assert msg_type in sent["content"]

        test_broker._transport.send_control.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_guard_allows_supported_control(self, test_broker):
        """Supported control messages pass through the guard."""
        test_broker._transport.capabilities = TransportCapabilities(interrupt=True)

        await test_broker._dispatch_browser_message({"type": "interrupt"})

        test_broker._transport.send_control.assert_called_once_with("interrupt")

    @pytest.mark.asyncio
    async def test_dispatch_terminal_controls(self, test_broker):
        """Interactive terminal controls pass through when the transport supports them."""
        test_broker._transport.capabilities = TransportCapabilities(
            terminal_input=True,
            terminal_keys=True,
            terminal_resize=True,
            slash_commands=True,
        )

        await test_broker._dispatch_browser_message(
            {"type": "terminal_input", "data": "/help", "enter": True, "pane_id": "%1"}
        )
        await test_broker._dispatch_browser_message(
            {"type": "terminal_key", "key": "Up", "pane_id": "%1"}
        )
        await test_broker._dispatch_browser_message(
            {"type": "terminal_resize", "cols": 120, "rows": 40, "pane_id": "%1"}
        )
        await test_broker._dispatch_browser_message(
            {
                "type": "slash_command",
                "command": "compact",
                "arguments": "now",
                "pane_id": "%1",
            }
        )

        assert test_broker._transport.send_control.call_args_list[0].args == ("terminal_input",)
        assert test_broker._transport.send_control.call_args_list[0].kwargs == {
            "data": "/help",
            "enter": True,
            "pane_id": "%1",
        }
        assert test_broker._transport.send_control.call_args_list[1].args == ("terminal_key",)
        assert test_broker._transport.send_control.call_args_list[1].kwargs == {
            "key": "Up",
            "keys": [],
            "pane_id": "%1",
        }
        assert test_broker._transport.send_control.call_args_list[2].args == ("terminal_resize",)
        assert test_broker._transport.send_control.call_args_list[2].kwargs == {
            "cols": 120,
            "rows": 40,
            "pane_id": "%1",
        }
        assert test_broker._transport.send_control.call_args_list[3].args == ("slash_command",)
        assert test_broker._transport.send_control.call_args_list[3].kwargs == {
            "command": "compact",
            "arguments": "now",
            "pane_id": "%1",
        }

    @pytest.mark.asyncio
    async def test_dispatch_discovers_slash_commands(self, test_broker):
        """Browser can request slash commands over the session WebSocket."""
        test_broker._transport.capabilities = TransportCapabilities(slash_commands=True)
        test_broker._transport.discover_slash_commands = AsyncMock(
            return_value=[
                {
                    "name": "/workflows",
                    "command": "workflows",
                    "description": "Browse workflows",
                    "kind": "command",
                    "source": "tmux_autocomplete",
                }
            ]
        )
        sender_ws = AsyncMock()

        await test_broker._dispatch_browser_message(
            {"type": "discover_slash_commands", "refresh": True},
            sender_ws=sender_ws,
        )

        test_broker._transport.discover_slash_commands.assert_awaited_once_with(refresh=True)
        sender_ws.send_json.assert_awaited_once_with(
            {
                "type": "slash_commands",
                "commands": [
                    {
                        "name": "/workflows",
                        "command": "workflows",
                        "description": "Browse workflows",
                        "kind": "command",
                        "source": "tmux_autocomplete",
                    }
                ],
                "count": 1,
            }
        )

    @pytest.mark.asyncio
    async def test_handle_claude_hook_normalizes_payload(self, test_broker):
        """Claude HTTP hooks are routed into the normal CLI event pipeline."""
        test_broker._transport = None
        test_broker._handle_cli_event = AsyncMock()

        await test_broker.handle_claude_hook(
            {"hook_event_name": "PostToolUse", "tool_name": "Read"}
        )

        test_broker._handle_cli_event.assert_awaited_once_with(
            {
                "type": "claude_hook",
                "event_type": "claude.hook",
                "hook_event_name": "PostToolUse",
                "payload": {"hook_event_name": "PostToolUse", "tool_name": "Read"},
            }
        )

    @pytest.mark.asyncio
    async def test_handle_claude_hook_delegates_to_transport_handler(self, test_broker):
        """Interactive transports can convert hook payloads before broker fallback."""
        transport = MagicMock()
        transport.handle_claude_hook = AsyncMock(return_value=True)
        test_broker._transport = transport
        test_broker._handle_cli_event = AsyncMock()

        payload = {"hook_event_name": "Stop", "last_assistant_message": "done"}
        await test_broker.handle_claude_hook(payload)

        transport.handle_claude_hook.assert_awaited_once_with(payload)
        test_broker._handle_cli_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_guard_no_sender_ws_still_blocks(self, test_broker):
        """Unsupported control is blocked even without sender_ws (no crash)."""
        test_broker._transport.capabilities = TransportCapabilities()  # all False

        await test_broker._dispatch_browser_message({"type": "interrupt"})

        test_broker._transport.send_control.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_permission_response_not_guarded(self, test_broker):
        """permission_response is not in the guard map and always passes."""
        test_broker._transport.capabilities = TransportCapabilities()  # all False

        await test_broker._dispatch_browser_message(
            {
                "type": "permission_response",
                "request_id": "req-1",
                "behavior": "allow",
                "updated_input": {},
            }
        )
        test_broker._transport.send_control_response.assert_called_once()


class TestFastAPIEndpoints:
    """Tests for FastAPI endpoints."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        yield client
        client.close()

    def test_health_endpoint(self, client, monkeypatch):
        broker.session_id = "test-123"
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["session_id"] == "test-123"

    def test_ready_endpoint_not_ready(self, client):
        broker._transport = None
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False

    def test_ready_endpoint_ready(self, client):
        broker._transport = MagicMock()
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        broker._transport = None

    def test_logs_endpoint(self, client):
        _log_buffer.clear()
        _log_buffer.append(
            {
                "time": "",
                "timestamp": 1000.0,
                "level": "INFO",
                "logger": "test",
                "message": "hello from test",
            }
        )
        response = client.get("/api/logs?lines=10&level=INFO")
        assert response.status_code == 200
        data = response.json()
        assert data["returned"] >= 1
        msgs = [e["message"] for e in data["lines"]]
        assert "hello from test" in msgs

    def test_aggregate_logs_endpoint(self, client, tmp_path):
        original_workspace = broker.workspace_dir
        broker.workspace_dir = str(tmp_path)
        try:
            (tmp_path / ".flock" / "logs").mkdir(parents=True)
            (tmp_path / ".skuld.log").write_text(
                "2026-05-01 15:19:48,121 - skuld.broker - INFO - Starting Skuld broker\n",
                encoding="utf-8",
            )
            (tmp_path / ".flock" / "logs" / "coder.log").write_text(
                (
                    "2026-05-01 15:19:58,326 ravn.drive_loop ERROR "
                    "drive_loop: task failed after 3 retries\n"
                ),
                encoding="utf-8",
            )

            response = client.get("/api/logs/aggregate?lines=10&level=INFO")
            assert response.status_code == 200
            data = response.json()
            assert data["session_id"] == broker.session_id
            assert {participant["id"] for participant in data["available_participants"]} == {
                "skuld",
                "coder",
            }
            assert [line["participant"] for line in data["lines"]] == ["skuld", "coder"]
        finally:
            broker.workspace_dir = original_workspace

    def test_capabilities_endpoint_returns_transport_caps(self, client):
        """GET /api/capabilities returns transport capabilities as JSON."""
        mock_transport = MagicMock()
        mock_transport.capabilities = TransportCapabilities(
            interrupt=True, set_model=True, cli_websocket=True
        )
        broker._transport = mock_transport

        response = client.get("/api/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert data["interrupt"] is True
        assert data["set_model"] is True
        assert data["cli_websocket"] is True
        assert data["rewind_files"] is False
        broker._transport = None

    def test_capabilities_endpoint_503_no_transport(self, client):
        """GET /api/capabilities returns 503 when transport not initialized."""
        broker._transport = None
        response = client.get("/api/capabilities")
        assert response.status_code == 503

    def test_slash_commands_endpoint_returns_discovered_commands(self, client):
        """GET /api/slash-commands returns normalized transport commands."""
        mock_transport = MagicMock()
        mock_transport.capabilities = TransportCapabilities(slash_commands=True)
        mock_transport.discover_slash_commands = AsyncMock(
            return_value=[
                {
                    "name": "/deep-research",
                    "command": "deep-research",
                    "description": "Deep research",
                    "kind": "workflow",
                    "source": "tmux_autocomplete",
                }
            ]
        )
        broker._transport = mock_transport

        response = client.get("/api/slash-commands?refresh=true")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["commands"][0]["name"] == "/deep-research"
        assert data["commands"][0]["kind"] == "workflow"
        mock_transport.discover_slash_commands.assert_awaited_once_with(refresh=True)
        broker._transport = None

    def test_send_slash_command_endpoint_uses_transport_control(self, client):
        """POST /api/slash-commands/send sends a terminal slash command."""
        mock_transport = MagicMock()
        mock_transport.capabilities = TransportCapabilities(slash_commands=True)
        mock_transport.send_control = AsyncMock()
        broker._transport = mock_transport

        response = client.post(
            "/api/slash-commands/send",
            json={"command": "workflows", "arguments": "--all", "pane_id": "%1"},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "sent", "command": "/workflows"}
        mock_transport.send_control.assert_awaited_once_with(
            "slash_command",
            command="workflows",
            arguments="--all",
            pane_id="%1",
        )
        broker._transport = None

    def test_slash_commands_endpoint_501_when_unsupported(self, client):
        """Slash command APIs report unsupported transports clearly."""
        mock_transport = MagicMock()
        mock_transport.capabilities = TransportCapabilities()
        broker._transport = mock_transport

        response = client.get("/api/slash-commands")

        assert response.status_code == 501
        broker._transport = None

    def test_logs_endpoint_level_filter(self, client):
        _log_buffer.clear()
        _log_buffer.append(
            {"time": "", "timestamp": 1.0, "level": "DEBUG", "logger": "x", "message": "dbg"}
        )
        _log_buffer.append(
            {"time": "", "timestamp": 2.0, "level": "ERROR", "logger": "x", "message": "err"}
        )
        response = client.get("/api/logs?lines=10&level=ERROR")
        assert response.status_code == 200
        data = response.json()
        msgs = [e["message"] for e in data["lines"]]
        assert "err" in msgs
        assert "dbg" not in msgs


class TestCORSMiddleware:
    """Tests for CORS middleware on the Skuld broker."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        yield client
        client.close()

    def test_cors_allows_all_origins(self, client):
        """CORS preflight should succeed for any origin."""
        response = client.options(
            "/api/logs",
            headers={
                "Origin": "https://hlidskjalf.valhalla.asgard.niuu.world",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code in (200, 204, 400)
        assert response.headers.get("access-control-allow-origin") in (
            "*",
            "https://hlidskjalf.valhalla.asgard.niuu.world",
        )

    def test_cors_headers_on_get(self, client):
        """Regular GET requests include CORS response headers."""
        _log_buffer.clear()
        response = client.get(
            "/api/logs",
            headers={"Origin": "https://hlidskjalf.valhalla.asgard.niuu.world"},
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") in (
            "*",
            "https://hlidskjalf.valhalla.asgard.niuu.world",
        )


class TestReportUsage:
    """Tests for Broker._report_usage."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-abc", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://volundr.test:80",
        )
        test_broker = Broker(settings=settings)
        yield test_broker
        if isinstance(test_broker._http_client, httpx.AsyncClient):
            asyncio.run(test_broker._http_client.aclose())
            test_broker._http_client = None

    @pytest.mark.asyncio
    async def test_report_usage_posts_to_api(self, test_broker):
        result_data = {
            "modelUsage": {
                "claude-opus-4-5-20251101": {
                    "inputTokens": 3,
                    "outputTokens": 12,
                    "cacheReadInputTokens": 100,
                    "cacheCreationInputTokens": 50,
                    "costUSD": 0.05,
                }
            }
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client

        await test_broker._report_usage(result_data)

        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        assert args[0] == "/api/v1/forge/sessions/sess-abc/usage"
        payload = kwargs["json"]
        assert payload["tokens"] == 3 + 12 + 100 + 50
        assert payload["provider"] == "cloud"
        assert payload["model"] == "claude-opus-4-5-20251101"
        assert payload["cost"] == 0.05

    @pytest.mark.asyncio
    async def test_report_activity_state_posts_to_forge_route(self, test_broker):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.url = "http://volundr.test/api/v1/forge/sessions/sess-abc/activity"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker.volundr_api_url = "http://volundr.test:80"

        await test_broker._report_activity_state("active")

        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        assert args[0] == "/api/v1/forge/sessions/sess-abc/activity"
        assert kwargs["json"]["state"] == "active"

    @pytest.mark.asyncio
    async def test_report_usage_skips_when_no_url(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "x", "workspace_dir": str(tmp_path)},
            volundr_api_url="",
        )
        b = Broker(settings=settings)

        mock_client = AsyncMock()
        b._http_client = mock_client

        await b._report_usage({"modelUsage": {"m": {"inputTokens": 1, "outputTokens": 1}}})
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_report_usage_handles_http_error(self, test_broker):
        result_data = {
            "modelUsage": {
                "claude-sonnet-4-20250514": {
                    "inputTokens": 10,
                    "outputTokens": 20,
                }
            }
        }

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client

        # Should not raise
        await test_broker._report_usage(result_data)

    @pytest.mark.asyncio
    async def test_report_usage_empty_model_usage(self, test_broker):
        """Skip reporting when modelUsage is empty."""
        mock_client = AsyncMock()
        test_broker._http_client = mock_client

        await test_broker._report_usage({"modelUsage": {}})
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_report_usage_zero_tokens_skipped(self, test_broker):
        """Skip models with zero total tokens."""
        mock_client = AsyncMock()
        test_broker._http_client = mock_client

        await test_broker._report_usage(
            {"modelUsage": {"m": {"inputTokens": 0, "outputTokens": 0}}}
        )
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_report_usage_handles_exception(self, test_broker):
        """Network errors are caught without propagating."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        test_broker._http_client = mock_client

        # Should not raise
        await test_broker._report_usage(
            {"modelUsage": {"m": {"inputTokens": 10, "outputTokens": 5}}}
        )

    @pytest.mark.asyncio
    async def test_get_http_client_lazy_init(self, test_broker):
        """HTTP client is created lazily on first use."""
        assert test_broker._http_client is None
        client = await test_broker._get_http_client()
        assert client is not None
        assert test_broker._http_client is client

        # Second call returns same instance
        client2 = await test_broker._get_http_client()
        assert client2 is client
        await client.aclose()

    @pytest.mark.asyncio
    async def test_get_http_client_uses_external_token_for_auth(self, tmp_path, monkeypatch):
        """HTTP client uses explicit external bearer token auth."""
        monkeypatch.setenv("VOLUNDR_EXTERNAL_API_TOKEN", "test-external-token")
        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://volundr-internal.volundr.svc",
        )
        b = Broker(settings=settings)
        headers = b._build_auth_headers()
        assert headers["Authorization"] == "Bearer test-external-token"


class TestSessionArtifacts:
    """Tests for SessionArtifacts accumulator."""

    def test_record_tool_use_extracts_file_paths(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {
            "content": [
                {
                    "type": "tool_use",
                    "input": {"file_path": "/src/main.py"},
                },
                {
                    "type": "tool_use",
                    "input": {"path": "/tests/test_main.py"},
                },
                {
                    "type": "text",
                    "text": "I edited the files",
                },
            ]
        }
        artifacts.record_tool_use(data)
        assert artifacts.files_changed == ["/src/main.py", "/tests/test_main.py"]

    def test_record_tool_use_deduplicates(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {"content": [{"type": "tool_use", "input": {"file_path": "/src/main.py"}}]}
        artifacts.record_tool_use(data)
        artifacts.record_tool_use(data)
        assert artifacts.files_changed == ["/src/main.py"]

    def test_record_tool_use_empty_content(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        artifacts.record_tool_use({"content": []})
        artifacts.record_tool_use({})
        assert artifacts.files_changed == []

    def test_record_result_increments_turns(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        assert artifacts.turn_count == 0
        artifacts.record_result()
        artifacts.record_result()
        assert artifacts.turn_count == 2

    def test_duration_seconds(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        # duration should be >= 0
        assert artifacts.duration_seconds >= 0

    def test_record_tool_use_sdk_message_format(self):
        """SDK WebSocket transport nests content under message.content."""
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/src/main.py"},
                        "id": "tu_1",
                    },
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": "/src/new_file.py"},
                        "id": "tu_2",
                    },
                ]
            },
        }
        events = artifacts.record_tool_use(data)
        assert artifacts.files_changed == ["/src/main.py", "/src/new_file.py"]
        assert len(events) == 2

    def test_record_tool_use_sdk_format_no_top_level_content(self):
        """When top-level content is absent, falls back to message.content."""
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/src/fix.py"},
                        "id": "tu_3",
                    },
                ]
            },
        }
        artifacts.record_tool_use(data)
        assert artifacts.files_changed == ["/src/fix.py"]

    def test_record_tool_use_prefers_top_level_content(self):
        """When both top-level and message.content exist, uses top-level."""
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {
            "content": [
                {
                    "type": "tool_use",
                    "input": {"file_path": "/src/top.py"},
                },
            ],
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "input": {"file_path": "/src/nested.py"},
                    },
                ]
            },
        }
        artifacts.record_tool_use(data)
        assert artifacts.files_changed == ["/src/top.py"]
        assert "/src/nested.py" not in artifacts.files_changed


class TestHandleCliEventArtifacts:
    """Tests for artifact accumulation in _handle_cli_event."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "test-session", "workspace_dir": str(tmp_path)},
            transport="subprocess",
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_assistant_event_records_tool_use(self, test_broker):
        data = {
            "type": "assistant",
            "content": [
                {"type": "tool_use", "input": {"file_path": "/src/app.py"}},
            ],
        }
        await test_broker._handle_cli_event(data)
        assert "/src/app.py" in test_broker._artifacts.files_changed

    @pytest.mark.asyncio
    async def test_result_event_increments_turn_count(self, test_broker):
        test_broker.volundr_api_url = ""  # disable usage reporting
        data = {"type": "result", "modelUsage": {}}
        await test_broker._handle_cli_event(data)
        assert test_broker._artifacts.turn_count == 1


class TestHandleCliEventSdkFormat:
    """Tests for artifact accumulation from SDK WebSocket message format."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "test-session-sdk", "workspace_dir": str(tmp_path)},
            transport="subprocess",
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_assistant_event_sdk_format_records_tool_use(self, test_broker):
        """SDK format: content nested under message.content."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/src/module.py"},
                        "id": "tu_sdk_1",
                    },
                ]
            },
        }
        await test_broker._handle_cli_event(data)
        assert "/src/module.py" in test_broker._artifacts.files_changed


class TestHandleCliEventTraceSpans:
    """Tests for trace child spans in normal assistant sessions."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "trace-session", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://volundr.test:80",
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_assistant_tool_use_starts_child_tool_span(self, test_broker):
        assistant_span_id = uuid.uuid4()
        tool_span_id = uuid.uuid4()
        test_broker._trace_session_span_id = uuid.uuid4()
        test_broker._trace_assistant_span_id = assistant_span_id

        with patch.object(
            test_broker,
            "_start_trace_span",
            new_callable=AsyncMock,
            return_value=tool_span_id,
        ) as mock_start:
            await test_broker._handle_cli_event(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tool-123",
                                "name": "Bash",
                                "input": {"command": "npm test"},
                            }
                        ]
                    },
                }
            )

        mock_start.assert_awaited_once()
        assert mock_start.await_args.kwargs["kind"] == "tool.call"
        assert mock_start.await_args.kwargs["parent_span_id"] == assistant_span_id
        assert mock_start.await_args.kwargs["attributes"]["tool_use_id"] == "tool-123"
        assert mock_start.await_args.kwargs["attributes"]["tool_input"]["command"] == "npm test"
        assert test_broker._trace_assistant_tool_spans["tool-123"] == tool_span_id

    @pytest.mark.asyncio
    async def test_tool_result_user_event_closes_child_span_without_closing_turn(
        self,
        test_broker,
    ):
        assistant_span_id = uuid.uuid4()
        tool_span_id = uuid.uuid4()
        test_broker._trace_assistant_span_id = assistant_span_id
        test_broker._trace_assistant_tool_spans["tool-123"] = tool_span_id
        test_broker._trace_assistant_tool_order.append("tool-123")
        test_broker._assistant_pending_commands["tool-123"] = "npm test"

        with patch.object(
            test_broker,
            "_finish_trace_span",
            new_callable=AsyncMock,
        ) as mock_finish:
            await test_broker._handle_cli_event(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool-123",
                                "content": "ok",
                                "is_error": False,
                            }
                        ]
                    },
                }
            )

        mock_finish.assert_awaited_once()
        assert mock_finish.await_args.args[0] == tool_span_id
        assert mock_finish.await_args.kwargs["status"] == "completed"
        assert mock_finish.await_args.kwargs["attributes"]["command"] == "npm test"
        assert test_broker._trace_assistant_span_id == assistant_span_id
        assert test_broker._trace_assistant_tool_spans == {}


class TestGitDiffSummary:
    """Tests for Broker._git_diff_summary."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "test-diff", "workspace_dir": str(tmp_path)},
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_returns_committed_diff(self, test_broker):
        """HEAD~1..HEAD is tried first and returned when non-empty."""
        diff_text = "diff --git a/main.py b/main.py\n+hello"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(diff_text.encode(), b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await test_broker._git_diff_summary()
        assert "diff --git" in result

    @pytest.mark.asyncio
    async def test_falls_back_to_uncommitted_diff(self, test_broker):
        """When HEAD~1..HEAD is empty, falls back to diff HEAD."""
        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            proc = AsyncMock()
            if call_count == 0:
                # HEAD~1..HEAD returns empty
                proc.communicate = AsyncMock(return_value=(b"", b""))
            else:
                # diff HEAD returns content
                proc.communicate = AsyncMock(return_value=(b"diff --git uncommitted\n+new", b""))
            call_count += 1
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await test_broker._git_diff_summary()
        assert "uncommitted" in result
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_truncates_large_diff(self, test_broker):
        test_broker._settings.mesh.diff_max_bytes = 100
        diff_text = "x" * 10000
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(diff_text.encode(), b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await test_broker._git_diff_summary()
        assert len(result) < 200
        assert "truncated" in result

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self, test_broker):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("no git")):
            result = await test_broker._git_diff_summary()
        assert result == ""

    @pytest.mark.asyncio
    async def test_uses_config_timeout(self, test_broker):
        """Timeout value comes from settings.mesh.diff_timeout_s."""
        test_broker._settings.mesh.diff_timeout_s = 5.0
        diff_text = "diff content"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(diff_text.encode(), b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", wraps=asyncio.wait_for) as mock_wait:
                result = await test_broker._git_diff_summary()
                assert mock_wait.call_args[1]["timeout"] == 5.0
        assert result == "diff content"


class TestPublishMeshOutcome:
    """Tests for Broker._publish_mesh_outcome with enriched payload."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={
                "id": "test-mesh-pub",
                "workspace_dir": str(tmp_path),
                "initial_prompt": "Fix the login bug",
            },
        )
        b = Broker(settings=settings)
        b._artifacts.files_changed = ["/src/auth.py", "/src/login.py"]
        b._artifacts.turn_count = 5
        return b

    @pytest.mark.asyncio
    async def test_payload_includes_workspace_and_task(self, test_broker):
        mock_mesh = AsyncMock()
        mock_adapter = MagicMock()
        mock_adapter.peer_id = "peer-1"
        mock_adapter._mesh = mock_mesh
        test_broker._mesh_adapter = mock_adapter

        with patch.object(test_broker, "_git_diff_summary", return_value="diff content"):
            await test_broker._publish_mesh_outcome()

        call_args = mock_mesh.publish.call_args
        event = call_args[0][0]
        payload = event.payload

        assert payload["workspace_path"] == test_broker.workspace_dir
        assert payload["task_description"] == "Fix the login bug"
        assert payload["diff_summary"] == "diff content"
        assert payload["files_changed"] == ["/src/auth.py", "/src/login.py"]
        assert "5 turns" in payload["summary"]
        assert "2 files" in payload["summary"]

    @pytest.mark.asyncio
    async def test_no_mesh_adapter_returns_early(self, test_broker):
        test_broker._mesh_adapter = None
        # Should not raise
        await test_broker._publish_mesh_outcome()

    @pytest.mark.asyncio
    async def test_payload_omits_empty_fields(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SKULD__SESSION__INITIAL_PROMPT", raising=False)
        settings = SkuldSettings(
            session={
                "id": "test-mesh-empty",
                "workspace_dir": str(tmp_path),
                "initial_prompt": "",
            },
        )
        b = Broker(settings=settings)
        mock_mesh = AsyncMock()
        mock_adapter = MagicMock()
        mock_adapter.peer_id = "peer-2"
        mock_adapter._mesh = mock_mesh
        b._mesh_adapter = mock_adapter
        b._room_bridge = None

        with patch.object(b, "_git_diff_summary", return_value=""):
            await b._publish_mesh_outcome()

        event = mock_mesh.publish.call_args[0][0]
        payload = event.payload
        assert "task_description" not in payload
        assert "diff_summary" not in payload
        assert "files_changed" not in payload


class TestReportChronicle:
    """Tests for Broker._report_chronicle."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-chronicle", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://volundr.test:80",
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_report_chronicle_posts_to_api(self, test_broker):
        # Stop-summarization is opt-in (OFF by default); this test exercises
        # the enabled path.
        test_broker._settings.chronicle_on_stop_enabled = True
        test_broker._artifacts.turn_count = 3
        test_broker._artifacts.files_changed = ["/src/main.py"]

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._transport = None  # skip AI summary

        await test_broker._report_chronicle()

        # Two POST calls: chronicle report + session_stop pipeline event
        assert mock_client.post.call_count == 2
        chronicle_call = mock_client.post.call_args_list[0]
        args, kwargs = chronicle_call
        assert args[0] == "/api/v1/forge/sessions/sess-chronicle/chronicle"
        payload = kwargs["json"]
        assert "duration_seconds" in payload
        assert payload["key_changes"] == ["/src/main.py"]

        pipeline_call = mock_client.post.call_args_list[1]
        p_args, p_kwargs = pipeline_call
        assert p_args[0] == "/api/v1/forge/events"
        assert p_kwargs["json"]["event_type"] == "session_stop"

    @pytest.mark.asyncio
    async def test_report_chronicle_skips_when_no_url(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "x", "workspace_dir": str(tmp_path)},
            volundr_api_url="",
        )
        b = Broker(settings=settings)
        b._artifacts.turn_count = 1

        mock_client = AsyncMock()
        b._http_client = mock_client

        await b._report_chronicle()
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_to_session_uses_forge_route(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        monkeypatch.setattr(broker, "volundr_api_url", "http://volundr.test:80")
        monkeypatch.setattr(broker, "_get_http_client", AsyncMock(return_value=mock_client))

        response = await send_message_to_session(
            _SendMessageRequest(session_id="sess-target", content="hello from broker")
        )

        assert response == {"status": "sent", "target_session_id": "sess-target"}
        mock_client.post.assert_awaited_once_with(
            "/api/v1/forge/sessions/sess-target/messages",
            json={"content": "hello from broker"},
        )

    @pytest.mark.asyncio
    async def test_report_chronicle_skips_when_no_turns(self, test_broker):
        mock_client = AsyncMock()
        test_broker._http_client = mock_client

        await test_broker._report_chronicle()
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_report_chronicle_skipped_by_default(self, test_broker):
        """Stop summarization is OFF by default — no chronicle POST fires."""
        test_broker._artifacts.turn_count = 3
        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        test_broker._http_client = mock_client
        test_broker._transport = None

        await test_broker._report_chronicle()

        assert mock_client.post.call_count == 0

    async def test_report_chronicle_posts_for_flock_outcome_without_turns(self, test_broker):
        test_broker._settings.chronicle_on_stop_enabled = True
        test_broker._artifacts.structured_outcome = {
            "summary": "Reviewer approved the changes",
            "files_changed": ["proofs/marker.txt"],
        }
        test_broker._artifacts.files_changed = ["proofs/marker.txt"]

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._transport = None

        await test_broker._report_chronicle()

        assert mock_client.post.call_count == 2
        payload = mock_client.post.call_args_list[0].kwargs["json"]
        assert payload["summary"] == "Reviewer approved the changes"
        assert payload["key_changes"] == ["proofs/marker.txt"]

    @pytest.mark.asyncio
    async def test_report_chronicle_handles_exception(self, test_broker):
        test_broker._artifacts.turn_count = 1
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        test_broker._http_client = mock_client
        test_broker._transport = None

        # Should not raise
        await test_broker._report_chronicle()

    @pytest.mark.asyncio
    async def test_report_chronicle_handles_http_error(self, test_broker):
        test_broker._artifacts.turn_count = 1

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client
        test_broker._transport = None

        # Should not raise
        await test_broker._report_chronicle()


class TestGenerateSummary:
    """Tests for Broker._generate_summary."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://volundr.test:80",
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_generate_summary_no_transport(self, test_broker):
        test_broker._transport = None
        test_broker._artifacts.files_changed = ["/src/app.py"]

        result = await test_broker._generate_summary()

        assert result["summary"] is None
        assert result["key_changes"] == ["/src/app.py"]

    @pytest.mark.asyncio
    async def test_generate_summary_transport_not_alive(self, test_broker):
        mock_transport = MagicMock()
        mock_transport.is_alive = False
        test_broker._transport = mock_transport

        result = await test_broker._generate_summary()

        assert result["summary"] is None

    @pytest.mark.asyncio
    async def test_generate_summary_parses_json_response(self, test_broker):
        mock_transport = AsyncMock()
        mock_transport.is_alive = True
        mock_transport.last_result = {
            "result": '{"summary": "Did stuff", '
            '"key_changes": ["a.py: edited"], '
            '"unfinished_work": null}'
        }
        test_broker._transport = mock_transport

        result = await test_broker._generate_summary()

        assert result["summary"] == "Did stuff"
        assert result["key_changes"] == ["a.py: edited"]
        assert result["unfinished_work"] is None

    @pytest.mark.asyncio
    async def test_generate_summary_handles_bad_json(self, test_broker):
        mock_transport = AsyncMock()
        mock_transport.is_alive = True
        mock_transport.last_result = {"result": "not json at all"}
        test_broker._transport = mock_transport
        test_broker._artifacts.files_changed = ["/fallback.py"]

        result = await test_broker._generate_summary()

        assert result["summary"] is None
        assert result["key_changes"] == ["/fallback.py"]


class TestShutdownWithChronicle:
    """Tests for shutdown calling _report_chronicle before transport.stop()."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://volundr.test:80",
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_shutdown_calls_report_chronicle(self, test_broker):
        call_order = []

        async def mock_report():
            call_order.append("report_chronicle")

        async def mock_stop():
            call_order.append("transport_stop")

        mock_transport = AsyncMock()
        mock_transport.stop = mock_stop
        test_broker._transport = mock_transport

        with patch.object(test_broker, "_report_chronicle", side_effect=mock_report):
            await test_broker.shutdown()

        assert "report_chronicle" in call_order
        assert "transport_stop" in call_order
        # Chronicle report must happen BEFORE transport stop
        assert call_order.index("report_chronicle") < call_order.index("transport_stop")


class TestShutdownEdgeCases:
    """Tests for shutdown edge cases."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://volundr.test:80",
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_shutdown_closes_http_client(self, test_broker):
        mock_client = AsyncMock()
        test_broker._http_client = mock_client

        await test_broker.shutdown()
        mock_client.aclose.assert_called_once()
        assert test_broker._http_client is None

    @pytest.mark.asyncio
    async def test_shutdown_close_channel_exception_ignored(self, test_broker):
        """Channel close errors during shutdown are silently ignored."""
        bad_ch = AsyncMock()
        bad_ch.channel_type = "browser"
        bad_ch.is_open = True
        bad_ch.close.side_effect = Exception("already closed")
        test_broker._channels.add(bad_ch)

        # Should not raise
        await test_broker.shutdown()

    @pytest.mark.asyncio
    async def test_startup_with_volundr_api_url(self, test_broker):
        """Startup logs when volundr_api_url is set."""
        await test_broker.startup()
        assert test_broker._transport is not None
        assert test_broker.service_manager is not None


class TestHandleWebSocket:
    """Tests for Broker.handle_websocket and handle_cli_websocket."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "ws-session", "workspace_dir": str(tmp_path)},
            transport="sdk",
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_handle_websocket_no_transport(self, test_broker):
        """Returns error JSON when transport is not initialized."""
        mock_ws = AsyncMock()
        test_broker._transport = None

        await test_broker.handle_websocket(mock_ws)

        mock_ws.accept.assert_called_once()
        mock_ws.send_json.assert_called_once()
        sent = mock_ws.send_json.call_args[0][0]
        assert sent["type"] == "error"
        assert "not initialized" in sent["content"]

    @pytest.mark.asyncio
    async def test_handle_websocket_normal_flow(self, test_broker):
        """Browser connects, receives welcome, sends message, then disconnects."""
        mock_transport = AsyncMock()
        mock_transport.is_alive = True
        mock_transport.capabilities = TransportCapabilities()
        test_broker._transport = mock_transport

        mock_ws = AsyncMock()
        # First receive_json returns a message, second raises disconnect
        mock_ws.receive_json = AsyncMock(side_effect=[{"content": "hello"}, WebSocketDisconnect()])

        await test_broker.handle_websocket(mock_ws)
        # send_message runs in a background task; pump the loop so it runs.
        await asyncio.sleep(0)

        mock_ws.accept.assert_called_once()
        # Welcome message + no error
        calls = mock_ws.send_json.call_args_list
        assert any("Connected to session" in str(c) for c in calls)
        mock_transport.send_message.assert_called_once_with("hello")

    @pytest.mark.asyncio
    async def test_handle_websocket_sends_capabilities(self, test_broker):
        """Browser receives a capabilities message after welcome, before history."""
        mock_transport = AsyncMock()
        mock_transport.is_alive = True
        mock_transport.capabilities = TransportCapabilities(interrupt=True, set_model=True)
        test_broker._transport = mock_transport

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

        await test_broker.handle_websocket(mock_ws)

        # Collect all sent messages
        calls = [c[0][0] for c in mock_ws.send_json.call_args_list]
        # Find capabilities message
        caps_msgs = [c for c in calls if c.get("type") == "capabilities"]
        assert len(caps_msgs) == 1
        caps = caps_msgs[0]
        assert caps["interrupt"] is True
        assert caps["set_model"] is True
        assert caps["rewind_files"] is False

        # Capabilities should come after welcome (system) message
        types = [c.get("type") for c in calls]
        system_idx = types.index("system")
        caps_idx = types.index("capabilities")
        assert caps_idx > system_idx

    @pytest.mark.asyncio
    async def test_handle_websocket_replays_pending_permission_requests(self, test_broker):
        mock_transport = AsyncMock()
        mock_transport.is_alive = True
        mock_transport.capabilities = TransportCapabilities()
        test_broker._transport = mock_transport
        pending = {
            "type": "control_request",
            "request_id": "perm-replay",
            "tool": "Bash",
            "input": {"command": "./stop-dev"},
        }
        test_broker._pending_permission_requests["perm-replay"] = pending

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

        await test_broker.handle_websocket(mock_ws)

        calls = [c[0][0] for c in mock_ws.send_json.call_args_list]
        assert pending in calls

    @pytest.mark.asyncio
    async def test_handle_websocket_treats_not_connected_runtime_error_as_disconnect(
        self, test_broker
    ):
        mock_transport = AsyncMock()
        mock_transport.is_alive = True
        mock_transport.capabilities = TransportCapabilities()
        test_broker._transport = mock_transport

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(
            side_effect=RuntimeError('WebSocket is not connected. Need to call "accept" first.')
        )

        await test_broker.handle_websocket(mock_ws)

        error_messages = [
            call.args[0]
            for call in mock_ws.send_json.call_args_list
            if call.args and isinstance(call.args[0], dict) and call.args[0].get("type") == "error"
        ]
        assert error_messages == []

    @pytest.mark.asyncio
    async def test_handle_websocket_treats_welcome_send_disconnect_as_normal(self, test_broker):
        mock_transport = AsyncMock()
        mock_transport.is_alive = True
        mock_transport.capabilities = TransportCapabilities()
        test_broker._transport = mock_transport

        mock_ws = AsyncMock()
        mock_ws.send_json = AsyncMock(
            side_effect=RuntimeError('Cannot call "send" once a close message has been sent.')
        )

        await test_broker.handle_websocket(mock_ws)

        mock_ws.accept.assert_called_once()
        mock_ws.receive_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_websocket_starts_transport(self, test_broker):
        """Transport.start() is called when transport is not alive."""
        mock_transport = AsyncMock()
        mock_transport.is_alive = False
        mock_transport.capabilities = TransportCapabilities()
        test_broker._transport = mock_transport

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

        await test_broker.handle_websocket(mock_ws)

        mock_transport.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_websocket_does_not_start_transport_for_workflow_room_session(
        self, tmp_path
    ):
        settings = SkuldSettings(
            session={
                "id": "ws-session",
                "workspace_dir": str(tmp_path),
                "initial_prompt": "Do the work",
            },
            transport="sdk",
            room={"enabled": True},
            workflow_trigger={
                "enabled": True,
                "node_id": "trigger-1",
                "label": "Dispatch",
                "source": "manual dispatch",
                "event_type": "code.requested",
            },
        )
        broker = Broker(settings=settings)
        mock_transport = AsyncMock()
        mock_transport.is_alive = False
        mock_transport.capabilities = TransportCapabilities()
        broker._transport = mock_transport

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

        await broker.handle_websocket(mock_ws)

        mock_transport.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_websocket_dispatch_error(
        self, test_broker, caplog: pytest.LogCaptureFixture
    ):
        """Errors raised by transport.send_message are caught + logged.

        send_message now runs in a background task so the WS receive loop
        doesn't stall for the duration of a turn. The error is surfaced via
        ``_safe_transport_send``: log the exception and broadcast an error
        event to any still-open channels. Tests check the log; broadcasting
        is best-effort because by the time the background task runs, the WS
        the user typed from may already have disconnected.
        """
        mock_transport = AsyncMock()
        mock_transport.is_alive = True
        mock_transport.capabilities = TransportCapabilities()
        mock_transport.send_message.side_effect = RuntimeError("CLI error")
        test_broker._transport = mock_transport

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=[{"content": "hello"}, WebSocketDisconnect()])

        with caplog.at_level("ERROR"):
            await test_broker.handle_websocket(mock_ws)
            # Let the background send_message task run.
            await asyncio.sleep(0)

        assert mock_transport.send_message.await_count == 1
        assert "Transport send_message failed" in caplog.text

    @pytest.mark.asyncio
    async def test_handle_websocket_unexpected_exception(self, test_broker):
        """Unexpected exceptions are caught and cleaned up."""
        mock_transport = AsyncMock()
        mock_transport.is_alive = True
        mock_transport.capabilities = TransportCapabilities()
        test_broker._transport = mock_transport

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=RuntimeError("boom"))

        await test_broker.handle_websocket(mock_ws)

        # Channel should be removed from registry
        assert test_broker._channels.count == 0

    @pytest.mark.asyncio
    async def test_handle_cli_websocket_wrong_transport(self, test_broker):
        """Rejects CLI WS when transport does not support SDK WebSocket."""
        mock_transport = AsyncMock(spec=SubprocessTransport)
        mock_transport.capabilities = TransportCapabilities(session_resume=True)
        test_broker._transport = mock_transport
        mock_ws = AsyncMock()

        await test_broker.handle_cli_websocket(mock_ws, "ws-session")

        mock_ws.close.assert_called_once()
        assert mock_ws.close.call_args[1]["code"] == 1008

    @pytest.mark.asyncio
    async def test_handle_cli_websocket_codex_rejected(self, test_broker):
        """Rejects CLI WS for Codex transport (subprocess only)."""
        mock_transport = AsyncMock(spec=CodexSubprocessTransport)
        mock_transport.capabilities = TransportCapabilities()
        test_broker._transport = mock_transport
        mock_ws = AsyncMock()

        await test_broker.handle_cli_websocket(mock_ws, "ws-session")

        mock_ws.close.assert_called_once()
        assert mock_ws.close.call_args[1]["code"] == 1008

    @pytest.mark.asyncio
    async def test_handle_cli_websocket_session_mismatch(self, test_broker):
        """Rejects CLI WS when session ID doesn't match."""
        mock_transport = AsyncMock(spec=SdkWebSocketTransport)
        mock_transport.capabilities = TransportCapabilities(cli_websocket=True)
        test_broker._transport = mock_transport
        mock_ws = AsyncMock()

        await test_broker.handle_cli_websocket(mock_ws, "wrong-session")

        mock_ws.close.assert_called_once()
        assert mock_ws.close.call_args[1]["code"] == 1008

    @pytest.mark.asyncio
    async def test_handle_cli_websocket_success(self, test_broker):
        """CLI WS attaches to transport and waits for disconnect."""
        mock_transport = AsyncMock(spec=SdkWebSocketTransport)
        mock_transport.capabilities = TransportCapabilities(cli_websocket=True)
        test_broker._transport = mock_transport
        mock_ws = AsyncMock()

        await test_broker.handle_cli_websocket(mock_ws, "ws-session")

        mock_transport.attach_cli_websocket.assert_called_once_with(mock_ws)
        mock_transport.wait_for_cli_disconnect.assert_called_once()


class TestServiceAPIEndpoints:
    """Tests for service management API endpoints."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        yield client
        client.close()

    def test_create_service_no_manager(self, client):
        broker.service_manager = None
        response = client.post(
            "/api/services",
            json={"name": "test-svc", "command": "echo hello", "port": 3000},
        )
        assert response.status_code == 503

    def test_list_services_no_manager(self, client):
        broker.service_manager = None
        response = client.get("/api/services")
        assert response.status_code == 503

    def test_get_service_no_manager(self, client):
        broker.service_manager = None
        response = client.get("/api/services/foo")
        assert response.status_code == 503

    def test_delete_service_no_manager(self, client):
        broker.service_manager = None
        response = client.delete("/api/services/foo")
        assert response.status_code == 503

    def test_get_service_logs_no_manager(self, client):
        broker.service_manager = None
        response = client.get("/api/services/foo/logs")
        assert response.status_code == 503

    def test_restart_service_no_manager(self, client):
        broker.service_manager = None
        response = client.post("/api/services/foo/restart")
        assert response.status_code == 503

    def test_get_service_not_found(self, client):
        mock_manager = AsyncMock()
        mock_manager.get_service = AsyncMock(return_value=None)
        broker.service_manager = mock_manager

        response = client.get("/api/services/nonexistent")
        assert response.status_code == 404
        broker.service_manager = None

    def test_delete_service_not_found(self, client):
        mock_manager = AsyncMock()
        mock_manager.remove_service = AsyncMock(return_value=False)
        broker.service_manager = mock_manager

        response = client.delete("/api/services/nonexistent")
        assert response.status_code == 404
        broker.service_manager = None

    def test_get_service_logs_not_found(self, client):
        mock_manager = AsyncMock()
        mock_manager.get_logs = AsyncMock(return_value=None)
        broker.service_manager = mock_manager

        response = client.get("/api/services/notfound/logs")
        assert response.status_code == 404
        broker.service_manager = None

    def test_restart_service_not_found(self, client):
        mock_manager = AsyncMock()
        mock_manager.restart_service = AsyncMock(return_value=None)
        broker.service_manager = mock_manager

        response = client.post("/api/services/notfound/restart")
        assert response.status_code == 404
        broker.service_manager = None

    def test_create_service_success(self, client):
        mock_status = {
            "name": "my-svc",
            "status": "running",
            "port": 3000,
            "command": "node app.js",
        }
        mock_manager = AsyncMock()
        mock_manager.add_service = AsyncMock(return_value=mock_status)
        broker.service_manager = mock_manager

        response = client.post(
            "/api/services",
            json={"name": "my-svc", "command": "node app.js", "port": 3000},
        )
        assert response.status_code == 200
        broker.service_manager = None

    def test_list_services_success(self, client):
        mock_manager = AsyncMock()
        mock_manager.list_services = AsyncMock(return_value=[])
        broker.service_manager = mock_manager

        response = client.get("/api/services")
        assert response.status_code == 200
        assert response.json() == []
        broker.service_manager = None

    def test_get_service_success(self, client):
        mock_status = {
            "name": "svc1",
            "status": "running",
            "port": 8080,
            "command": "python app.py",
        }
        mock_manager = AsyncMock()
        mock_manager.get_service = AsyncMock(return_value=mock_status)
        broker.service_manager = mock_manager

        response = client.get("/api/services/svc1")
        assert response.status_code == 200
        assert response.json()["name"] == "svc1"
        broker.service_manager = None

    def test_delete_service_success(self, client):
        mock_manager = AsyncMock()
        mock_manager.remove_service = AsyncMock(return_value=True)
        broker.service_manager = mock_manager

        response = client.delete("/api/services/svc1")
        assert response.status_code == 200
        assert response.json()["status"] == "removed"
        broker.service_manager = None

    def test_get_service_logs_success(self, client):
        mock_manager = AsyncMock()
        mock_manager.get_logs = AsyncMock(return_value="line1\nline2")
        broker.service_manager = mock_manager

        response = client.get("/api/services/svc1/logs?lines=50")
        assert response.status_code == 200
        assert response.json()["logs"] == "line1\nline2"
        broker.service_manager = None

    def test_restart_service_success(self, client):
        mock_status = {
            "name": "svc1",
            "status": "running",
            "port": 8080,
            "command": "python app.py",
            "restart_count": 1,
        }
        mock_manager = AsyncMock()
        mock_manager.restart_service = AsyncMock(return_value=mock_status)
        broker.service_manager = mock_manager

        response = client.post("/api/services/svc1/restart")
        assert response.status_code == 200
        assert response.json()["restart_count"] == 1
        broker.service_manager = None


class TestRecordToolUseReturnsEvents:
    """Tests for record_tool_use returning timeline-reportable events."""

    def test_returns_file_event_for_edit(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "/src/main.py"},
                },
            ]
        }
        events = artifacts.record_tool_use(data)
        assert len(events) == 1
        assert events[0]["type"] == "file"
        assert events[0]["label"] == "/src/main.py"
        assert events[0]["action"] == "modified"

    def test_returns_file_event_for_write(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": "/src/new.py"},
                },
            ]
        }
        events = artifacts.record_tool_use(data)
        assert len(events) == 1
        assert events[0]["type"] == "file"
        assert events[0]["action"] == "created"

    def test_returns_terminal_event_for_bash(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "python -m pytest tests/"},
                },
            ]
        }
        events = artifacts.record_tool_use(data)
        assert len(events) == 1
        assert events[0]["type"] == "terminal"
        assert "pytest" in events[0]["label"]

    def test_returns_git_event_for_git_commit(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": 'git commit -m "feat: add feature"'},
                },
            ]
        }
        events = artifacts.record_tool_use(data)
        assert len(events) == 1
        assert events[0]["type"] == "git"
        assert "git commit" in events[0]["label"]

    def test_returns_git_event_for_chained_commit(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": 'git add . && git commit -m "fix"'},
                },
            ]
        }
        events = artifacts.record_tool_use(data)
        assert len(events) == 1
        assert events[0]["type"] == "git"

    def test_returns_multiple_events(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "/src/a.py"},
                },
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "make test"},
                },
            ]
        }
        events = artifacts.record_tool_use(data)
        assert len(events) == 2
        assert events[0]["type"] == "file"
        assert events[1]["type"] == "terminal"

    def test_returns_empty_for_unknown_tools(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        data = {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "/src/a.py"},
                },
            ]
        }
        events = artifacts.record_tool_use(data)
        assert len(events) == 0
        # But file_path is still tracked
        assert artifacts.files_changed == ["/src/a.py"]

    def test_returns_empty_for_no_content(self):
        from skuld.broker import SessionArtifacts

        artifacts = SessionArtifacts()
        events = artifacts.record_tool_use({})
        assert events == []


class TestIsGitCommit:
    """Tests for _is_git_commit helper."""

    def test_simple_git_commit(self):
        from skuld.broker import _is_git_commit

        assert _is_git_commit('git commit -m "msg"') is True

    def test_git_commit_with_flags(self):
        from skuld.broker import _is_git_commit

        assert _is_git_commit("git commit --amend --no-edit") is True

    def test_chained_git_add_and_commit(self):
        from skuld.broker import _is_git_commit

        assert _is_git_commit('git add . && git commit -m "feat"') is True

    def test_git_config_prefix(self):
        from skuld.broker import _is_git_commit

        assert _is_git_commit('git -c user.name="x" commit -m "y"') is True

    def test_not_git_commit(self):
        from skuld.broker import _is_git_commit

        assert _is_git_commit("git status") is False
        assert _is_git_commit("git push origin main") is False
        assert _is_git_commit("python -m pytest") is False

    def test_empty_string(self):
        from skuld.broker import _is_git_commit

        assert _is_git_commit("") is False


class TestReportTimelineEvent:
    """Tests for Broker._report_timeline_event."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-tl", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://volundr.test:80",
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_posts_event_to_api(self, test_broker):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client

        event = {"t": 10, "type": "message", "label": "Turn 1", "tokens": 500}
        await test_broker._report_timeline_event(event)

        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        assert "/chronicles/sess-tl/timeline" in args[0]
        assert kwargs["json"]["type"] == "message"
        assert kwargs["json"]["tokens"] == 500

    @pytest.mark.asyncio
    async def test_skips_when_no_url(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "x", "workspace_dir": str(tmp_path)},
            volundr_api_url="",
        )
        b = Broker(settings=settings)
        mock_client = AsyncMock()
        b._http_client = mock_client

        await b._report_timeline_event({"t": 0, "type": "session", "label": "start"})
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_http_error(self, test_broker):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client

        # Should not raise
        await test_broker._report_timeline_event({"t": 0, "type": "session", "label": "start"})

    @pytest.mark.asyncio
    async def test_handles_exception(self, test_broker):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        test_broker._http_client = mock_client

        # Should not raise
        await test_broker._report_timeline_event({"t": 0, "type": "session", "label": "start"})


class TestReportSessionStart:
    """Tests for Broker._report_session_start."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-start", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://volundr.test:80",
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_reports_session_start_once(self, test_broker):
        with (
            patch.object(
                test_broker, "_report_timeline_event", new_callable=AsyncMock
            ) as mock_report,
            patch.object(test_broker, "_emit_pipeline_event", new_callable=AsyncMock),
        ):
            await test_broker._report_session_start()
            await test_broker._report_session_start()

            # Should only be called once (idempotent)
            mock_report.assert_called_once()
            event = mock_report.call_args[0][0]
            assert event["type"] == "session"
            assert event["t"] == 0

    @pytest.mark.asyncio
    async def test_sets_flag_after_first_call(self, test_broker):
        with (
            patch.object(test_broker, "_report_timeline_event", new_callable=AsyncMock),
            patch.object(test_broker, "_emit_pipeline_event", new_callable=AsyncMock),
        ):
            assert test_broker._session_start_reported is False
            await test_broker._report_session_start()
            assert test_broker._session_start_reported is True


class TestHandleCliEventTimeline:
    """Tests for timeline events in _handle_cli_event."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-evt", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://volundr.test:80",
        )
        return Broker(settings=settings)

    @pytest.mark.asyncio
    async def test_result_event_reports_message_timeline(self, test_broker):
        with (
            patch.object(
                test_broker, "_report_timeline_event", new_callable=AsyncMock
            ) as mock_report,
            patch.object(test_broker, "_report_usage", new_callable=AsyncMock),
        ):
            data = {
                "type": "result",
                "modelUsage": {
                    "claude-opus-4-6": {
                        "inputTokens": 100,
                        "outputTokens": 200,
                    }
                },
            }
            await test_broker._handle_cli_event(data)

            # Let background tasks complete
            await asyncio.sleep(0.05)

            # Should have reported a message timeline event
            calls = mock_report.call_args_list
            message_calls = [c for c in calls if c[0][0]["type"] == "message"]
            assert len(message_calls) == 1
            assert message_calls[0][0][0]["tokens"] == 300
            assert "Turn 1" in message_calls[0][0][0]["label"]

    @pytest.mark.asyncio
    async def test_assistant_event_reports_file_timeline(self, test_broker):
        with patch.object(
            test_broker, "_report_timeline_event", new_callable=AsyncMock
        ) as mock_report:
            data = {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/src/app.py"},
                    },
                ],
            }
            await test_broker._handle_cli_event(data)

            await asyncio.sleep(0.05)

            calls = mock_report.call_args_list
            file_calls = [c for c in calls if c[0][0]["type"] == "file"]
            assert len(file_calls) == 1
            assert file_calls[0][0][0]["label"] == "/src/app.py"

    @pytest.mark.asyncio
    async def test_assistant_bash_reports_terminal_timeline(self, test_broker):
        with patch.object(
            test_broker, "_report_timeline_event", new_callable=AsyncMock
        ) as mock_report:
            data = {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "npm test"},
                    },
                ],
            }
            await test_broker._handle_cli_event(data)

            await asyncio.sleep(0.05)

            calls = mock_report.call_args_list
            terminal_calls = [c for c in calls if c[0][0]["type"] == "terminal"]
            assert len(terminal_calls) == 1
            assert "npm test" in terminal_calls[0][0][0]["label"]

    @pytest.mark.asyncio
    async def test_result_with_zero_tokens_skips_timeline(self, test_broker):
        with (
            patch.object(
                test_broker, "_report_timeline_event", new_callable=AsyncMock
            ) as mock_report,
            patch.object(test_broker, "_report_usage", new_callable=AsyncMock),
        ):
            data = {"type": "result", "modelUsage": {}}
            await test_broker._handle_cli_event(data)

            await asyncio.sleep(0.05)

            mock_report.assert_not_called()


class TestPipelineEventEmission:
    """Tests for Broker._emit_pipeline_event and _classify_pipeline_event."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-pipeline", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://volundr.test:80",
        )
        return Broker(settings=settings)

    def test_classify_file_created(self):
        ev = {"type": "file", "action": "created"}
        assert Broker._classify_pipeline_event(ev) == "file_created"

    def test_classify_file_modified(self):
        ev = {"type": "file", "action": "modified"}
        assert Broker._classify_pipeline_event(ev) == "file_modified"

    def test_classify_file_deleted(self):
        ev = {"type": "file", "action": "deleted"}
        assert Broker._classify_pipeline_event(ev) == "file_deleted"

    def test_classify_file_default(self):
        assert Broker._classify_pipeline_event({"type": "file"}) == "file_modified"

    def test_classify_git(self):
        assert Broker._classify_pipeline_event({"type": "git"}) == "git_commit"

    def test_classify_git_push(self):
        assert Broker._classify_pipeline_event({"type": "git_push"}) == "git_push"

    def test_classify_terminal(self):
        assert Broker._classify_pipeline_event({"type": "terminal"}) == "terminal_command"

    def test_classify_unknown(self):
        assert Broker._classify_pipeline_event({"type": "something"}) == "tool_use"

    @pytest.mark.asyncio
    async def test_emit_pipeline_event_posts(self, test_broker):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client

        await test_broker._emit_pipeline_event(
            "file_modified",
            {"path": "/src/main.py"},
            tokens_in=10,
            model="claude-sonnet-4-20250514",
        )

        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        assert args[0] == "/api/v1/forge/events"
        payload = kwargs["json"]
        assert payload["event_type"] == "file_modified"
        assert payload["session_id"] == "sess-pipeline"
        assert payload["tokens_in"] == 10
        assert payload["model"] == "claude-sonnet-4-20250514"
        assert "timestamp" in payload
        assert "sequence" in payload

    @pytest.mark.asyncio
    async def test_emit_pipeline_event_skips_when_no_url(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "x", "workspace_dir": str(tmp_path)},
            volundr_api_url="",
        )
        b = Broker(settings=settings)
        mock_client = AsyncMock()
        b._http_client = mock_client

        await b._emit_pipeline_event("session_start", {})
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_emit_pipeline_event_handles_error(self, test_broker):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("network error"))
        test_broker._http_client = mock_client

        # Should not raise
        await test_broker._emit_pipeline_event("error", {"message": "test"})

    @pytest.mark.asyncio
    async def test_sequence_increments(self, test_broker):
        assert test_broker._next_sequence() == 0
        assert test_broker._next_sequence() == 1
        assert test_broker._next_sequence() == 2

    @pytest.mark.asyncio
    async def test_session_start_emits_pipeline_event(self, test_broker):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        test_broker._http_client = mock_client

        await test_broker._report_session_start()

        # Should have called timeline + pipeline events
        calls = mock_client.post.call_args_list
        pipeline_calls = [c for c in calls if c[0][0] == "/api/v1/forge/events"]
        assert len(pipeline_calls) == 1
        payload = pipeline_calls[0][1]["json"]
        assert payload["event_type"] == "session_start"


class TestTokenRedactFilter:
    """Tests for JWT redaction in log output."""

    def test_redacts_access_token_in_msg(self):
        """access_token values are replaced with [REDACTED]."""
        f = _TokenRedactFilter()
        record = logging.LogRecord(
            name="uvicorn",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="WebSocket /session?access_token=eyJhbGciOiJSUzI1NiJ9.payload.sig [accepted]",
            args=None,
            exc_info=None,
        )
        f.filter(record)
        assert "eyJ" not in record.msg
        assert "access_token=[REDACTED]" in record.msg
        assert "[accepted]" in record.msg

    def test_leaves_messages_without_token(self):
        """Messages without access_token are unchanged."""
        f = _TokenRedactFilter()
        original = "GET /api/files HTTP/1.1 200"
        record = logging.LogRecord(
            name="uvicorn",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=original,
            args=None,
            exc_info=None,
        )
        f.filter(record)
        assert record.msg == original

    def test_always_returns_true(self):
        """Filter returns True (keep the record, just redact)."""
        f = _TokenRedactFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="access_token=secret",
            args=None,
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_handles_non_string_msg(self):
        """Non-string msg is left alone without error."""
        f = _TokenRedactFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=12345,
            args=None,
            exc_info=None,
        )
        f.filter(record)
        assert record.msg == 12345

    def test_redacts_multiple_tokens_in_one_message(self):
        """Multiple tokens in one message are all redacted."""
        f = _TokenRedactFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="first access_token=abc123 second access_token=xyz789",
            args=None,
            exc_info=None,
        )
        f.filter(record)
        assert record.msg == "first access_token=[REDACTED] second access_token=[REDACTED]"

    def test_redacts_tokens_in_args(self):
        """Token-bearing values in structured log args are redacted too."""
        f = _TokenRedactFilter()
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="%s %s",
            args=("WebSocket", "/session?access_token=secret-token"),
            exc_info=None,
        )
        f.filter(record)
        assert record.args == ("WebSocket", "/session?access_token=[REDACTED]")

    @pytest.mark.asyncio
    async def test_filter_attached_during_lifespan(self):
        """Lifespan attaches redact filter to uvicorn loggers."""
        from skuld.broker import lifespan

        async def check():
            for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
                logging.getLogger(name).filters = []

            with patch.object(broker, "startup", new_callable=AsyncMock):
                with patch.object(broker, "shutdown", new_callable=AsyncMock):
                    async with lifespan(app):
                        for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
                            lgr = logging.getLogger(name)
                            has_redact = any(isinstance(f, _TokenRedactFilter) for f in lgr.filters)
                            assert has_redact, f"{name} missing _TokenRedactFilter"

        await check()


# ---------------------------------------------------------------------------
# Room bridge integration in Broker (NIU-602)
# ---------------------------------------------------------------------------


class TestBrokerRoomBridge:
    """Tests for RoomBridge wiring inside Broker."""

    @pytest.fixture
    def room_settings(self, tmp_path):
        return SkuldSettings(
            session={"id": "room-session", "workspace_dir": str(tmp_path)},
            transport="sdk",
            room={"enabled": True},
        )

    @pytest.fixture
    def no_room_settings(self, tmp_path):
        return SkuldSettings(
            session={"id": "noroom-session", "workspace_dir": str(tmp_path)},
            transport="sdk",
            room={"enabled": False},
        )

    def test_room_bridge_created_when_enabled(self, room_settings):
        from skuld.room_bridge import RoomBridge

        b = Broker(settings=room_settings)
        assert b._room_bridge is not None
        assert isinstance(b._room_bridge, RoomBridge)

    def test_room_bridge_none_when_disabled(self, no_room_settings):
        b = Broker(settings=no_room_settings)
        assert b._room_bridge is None

    @pytest.mark.asyncio
    async def test_join_heartbeat_leave_human_environment(self, room_settings):
        bus = InProcessBus()
        captured = EventCapture(bus, ["participant.*"])
        await captured.start()
        b = Broker(settings=room_settings, sleipnir_publisher=bus)

        joined = await b.join_human_environment(
            participant_id="human:jozef",
            display_name="Jozef",
            environment_id="cluster-a",
            role="owner",
            room_id="huddle-1",
        )
        heartbeat = await b.heartbeat_human_environment(
            participant_id="human:jozef",
            status="busy",
            wakefulness="wakeful",
            attention_state="reviewing",
        )
        await b.leave_human_environment(participant_id="human:jozef", reason="done")
        await bus.flush()

        assert joined["participant_type"] == "human"
        assert joined["authority_role"] == "owner"
        assert "change_autonomy" in joined["capabilities"]
        assert heartbeat["status"] == "busy"
        assert "human:jozef" not in b._room_bridge.participants
        assert [event.event_type for event in captured.events] == [
            event_registry.PARTICIPANT_JOINED,
            event_registry.PARTICIPANT_HEARTBEAT,
            event_registry.PARTICIPANT_LEFT,
        ]
        await captured.stop()

    @pytest.mark.asyncio
    async def test_join_human_environment_binds_capabilities_to_environment_authorities(
        self,
        room_settings,
    ):
        b = Broker(settings=room_settings)

        joined = await b.join_human_environment(
            participant_id="human:approver",
            display_name="Approver",
            environment_id="cluster-a",
            role="approver",
            environment_action_authorities=["autonomous"],
        )

        assert "approve" not in joined["capabilities"]
        assert "authorize_action" not in joined["capabilities"]
        assert joined["capabilities"] == ("view", "reply")

    @pytest.mark.asyncio
    async def test_human_room_message_preserves_participant_thread_metadata(self, room_settings):
        b = Broker(settings=room_settings)
        b._transport = AsyncMock()
        broadcast = AsyncMock()
        b._channels.broadcast = broadcast
        await b.join_human_environment(
            participant_id="human:teacher",
            display_name="Teacher",
            environment_id="cluster-a",
            role="teacher",
        )

        msg_id = await b.handle_human_room_message(
            "This was the wrong tier.",
            source="browser",
            participant_id="human:teacher",
            metadata={"thread_id": "thread-1", "root_correlation_id": "root-1"},
            deliver_to_transport=False,
        )

        assert b._conversation_turns[-1].id == msg_id
        assert b._conversation_turns[-1].participant_id == "human:teacher"
        assert b._conversation_turns[-1].thread_id == "thread-1"
        assert b._conversation_turns[-1].metadata["root_correlation_id"] == "root-1"
        event = broadcast.await_args.args[0]
        assert event["participantId"] == "human:teacher"
        assert event["threadId"] == "thread-1"
        assert event["metadata"]["root_correlation_id"] == "root-1"

    @pytest.mark.asyncio
    async def test_directed_room_message_preserves_metadata_to_target(self, room_settings):
        b = Broker(settings=room_settings)
        b._transport = AsyncMock()
        await b.join_human_environment(
            participant_id="human:approver",
            display_name="Approver",
            environment_id="cluster-a",
            role="approver",
        )
        ws = MagicMock()
        ws.send_text = AsyncMock()
        await b._room_bridge.register("valkyrie:k8s-a", "K8s Valkyrie", ws)

        await b.handle_directed_room_message(
            "valkyrie:k8s-a",
            "Approved, continue.",
            source="browser",
            metadata={
                "participant_id": "human:approver",
                "thread_id": "thread-approve",
                "root_correlation_id": "root-approve",
            },
        )

        payload = json.loads(ws.send_text.await_args.args[0])
        assert payload["type"] == "directed_message"
        assert payload["content"] == "Approved, continue."
        assert payload["metadata"]["thread_id"] == "thread-approve"
        assert payload["metadata"]["root_correlation_id"] == "root-approve"
        assert payload["metadata"]["participant_id"] == "human:approver"
        assert b._conversation_turns[-1].participant_id == "human:approver"
        assert b._conversation_turns[-1].thread_id == "thread-approve"

    @pytest.mark.asyncio
    async def test_room_capability_check_rejects_unauthorized_control(self, room_settings):
        b = Broker(settings=room_settings)
        await b.join_human_environment(
            participant_id="human:observer",
            display_name="Observer",
            environment_id="cluster-a",
            role="observer",
        )

        with pytest.raises(PermissionError):
            b.require_room_capability("human:observer", "authorize_action")

    @pytest.mark.asyncio
    async def test_joined_human_feedback_links_to_valkyrie_decision(self, room_settings):
        class _Memory:
            def __init__(self) -> None:
                self.episodes = []

            async def record_episode(self, episode):
                self.episodes.append(episode)

        bus = InProcessBus()
        memory = _Memory()
        recorder = EnvironmentFeedbackRecorder(subscriber=bus, publisher=bus, memory=memory)
        await recorder.start()
        b = Broker(settings=room_settings, sleipnir_publisher=bus)
        await b.join_human_environment(
            participant_id="human:owner",
            display_name="Owner",
            environment_id="cluster-a",
            role="owner",
        )

        await bus.publish(
            feedback_recorded(
                environment_id="cluster-a",
                target_event_id="attention-decision-1",
                feedback_type="useful",
                rating="correct",
                notes="Good escalation.",
                judgment_refs=["judgment-1"],
                court_decision_id="attention-decision-1",
                user_id="human:owner",
                responsible_valkyrie_id="valkyrie:k8s-a",
                source="human:owner",
                correlation_id="root-feedback",
            )
        )
        await bus.flush()

        assert len(memory.episodes) == 1
        structured = memory.episodes[0].structured_outcome
        assert structured["user_id"] == "human:owner"
        assert structured["judgment_refs"] == ["judgment-1"]
        assert structured["court_decision_id"] == "attention-decision-1"

        await recorder.stop()

    # -----------------------------------------------------------------------
    # directed_message dispatch
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_dispatch_directed_message_with_room_bridge(self, room_settings):
        b = Broker(settings=room_settings)
        b._transport = AsyncMock()
        b.handle_directed_room_message = AsyncMock()

        await b._dispatch_browser_message(
            {"type": "directed_message", "targetPeerId": "agent-1", "content": "Hi!"}
        )

        b.handle_directed_room_message.assert_awaited_once_with(
            "agent-1",
            "Hi!",
            source="browser",
            metadata=None,
        )

    @pytest.mark.asyncio
    async def test_dispatch_directed_message_empty_target_ignored(self, room_settings):
        b = Broker(settings=room_settings)
        b._transport = AsyncMock()
        mock_bridge = AsyncMock()
        b._room_bridge = mock_bridge

        await b._dispatch_browser_message(
            {"type": "directed_message", "targetPeerId": "", "content": "Hi!"}
        )

        mock_bridge.route_directed_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_directed_message_room_disabled_sends_error(self, no_room_settings):
        b = Broker(settings=no_room_settings)
        b._transport = AsyncMock()
        mock_ws = AsyncMock()

        await b._dispatch_browser_message(
            {"type": "directed_message", "targetPeerId": "p1", "content": "Hi!"},
            sender_ws=mock_ws,
        )

        mock_ws.send_json.assert_awaited_once()
        sent = mock_ws.send_json.call_args[0][0]
        assert sent["type"] == "error"

    @pytest.mark.asyncio
    async def test_handle_human_room_message_records_and_broadcasts(self, room_settings):
        b = Broker(settings=room_settings)
        transport = AsyncMock()
        transport.is_alive = False
        b._transport = transport
        assert b._room_bridge is not None
        await b._room_bridge.register("peer-1", "coder", AsyncMock(), display_name="Coder")
        broadcast = AsyncMock()
        b._channels = MagicMock()
        b._channels.broadcast = broadcast

        message_id = await b.handle_human_room_message("Hello from Telegram", source="telegram")

        assert message_id
        assert b._conversation_turns[-1].role == "user"
        assert b._conversation_turns[-1].content == "Hello from Telegram"
        broadcast.assert_awaited_once()
        sent = broadcast.await_args.args[0]
        assert sent["type"] == "user_confirmed"
        assert sent["content"] == "Hello from Telegram"
        assert sent["source"] == "telegram"
        transport.start.assert_awaited_once()
        transport.send_message.assert_awaited_once()
        outbound = transport.send_message.await_args.args[0]
        assert "Hello from Telegram" in outbound
        assert "You are Skuld, the observer/coordinator for an active flock session." in outbound
        assert "Coder (peer_id=peer-1, type=ravn" in outbound

    @pytest.mark.asyncio
    async def test_handle_directed_room_message_routes_to_target(self, room_settings):
        b = Broker(settings=room_settings)
        transport = AsyncMock()
        transport.is_alive = False
        b._transport = transport
        assert b._room_bridge is not None
        register_ws = AsyncMock()
        await b._room_bridge.register("peer-1", "coder", register_ws, display_name="Coder")
        broadcast = AsyncMock()
        b._channels = MagicMock()
        b._channels.broadcast = broadcast

        message_id = await b.handle_directed_room_message(
            "peer-1",
            "Please investigate this",
            source="telegram",
        )

        assert message_id
        assert b._conversation_turns[-1].content == "@coder Please investigate this"
        register_ws.send_text.assert_awaited_once()
        payload = json.loads(register_ws.send_text.await_args.args[0])
        assert payload["type"] == "directed_message"
        assert payload["content"] == "Please investigate this"
        transport.start.assert_not_awaited()
        transport.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_resend_initial_prompt_records_and_forwards_to_room_transport(
        self,
        tmp_path,
    ):
        settings = SkuldSettings(
            session={
                "id": "room-session",
                "workspace_dir": str(tmp_path),
                "initial_prompt": "Please investigate the cluster state.",
            },
            transport="sdk",
            room={"enabled": True},
        )
        b = Broker(settings=settings)
        transport = AsyncMock()
        transport.is_alive = False
        b._transport = transport
        broadcast = AsyncMock()
        b._channels = MagicMock()
        b._channels.broadcast = broadcast

        message_id = await b.handle_resend_initial_prompt(source="browser")

        assert message_id
        assert b._conversation_turns[-1].content == "Please investigate the cluster state."
        assert b._conversation_turns[-1].metadata["resend_prompt"] is True
        broadcast.assert_awaited_once()
        assert broadcast.await_args.args[0]["content"] == "Please investigate the cluster state."
        transport.start.assert_awaited_once()
        transport.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_transport_user_echo_does_not_duplicate_explicit_human_room_message(
        self, room_settings
    ):
        b = Broker(settings=room_settings)
        transport = AsyncMock()
        transport.is_alive = True
        b._transport = transport
        b._channels = MagicMock()
        b._channels.broadcast = AsyncMock()

        await b.handle_human_room_message("Hello from Telegram", source="telegram")
        assert len(b._conversation_turns) == 1

        await b._handle_cli_event({"type": "user", "message": {"content": "Hello from Telegram"}})

        assert len(b._conversation_turns) == 1

    @pytest.mark.asyncio
    async def test_explicit_human_room_response_is_room_only(self, room_settings):
        b = Broker(settings=room_settings)
        transport = AsyncMock()
        transport.is_alive = True
        b._transport = transport
        b._channels = MagicMock()
        b._channels.broadcast = AsyncMock()
        b._mesh_adapter = MagicMock()
        b._mesh_adapter.peer_id = "skuld-room"
        assert b._room_bridge is not None
        await b._room_bridge.register_mesh_peer(
            "skuld-room",
            "Skuld",
            display_name="Skuld",
            participant_type="skuld",
        )

        publish_mesh = AsyncMock()
        b._on_result_publish_mesh = publish_mesh

        await b.handle_human_room_message("Who is in this flock?", source="telegram")
        assert len(b._conversation_turns) == 1

        await b._handle_cli_event(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Skuld sees coder, reviewer, and verifier.",
                        }
                    ]
                },
            }
        )
        await b._handle_cli_event(
            {
                "type": "result",
                "result": "Skuld sees coder, reviewer, and verifier.",
                "modelUsage": {},
            }
        )

        assert b._channels.broadcast.await_count == 1
        assert publish_mesh.await_count == 0
        assert len(b._conversation_turns) == 2
        assert b._conversation_turns[-1].content == "Skuld sees coder, reviewer, and verifier."
        assert b._conversation_turns[-1].participant_id == "skuld-room"

    @pytest.mark.asyncio
    async def test_get_room_participants_returns_snapshot(self, room_settings):
        b = Broker(settings=room_settings)
        assert b._room_bridge is not None
        await b._room_bridge.register("peer-1", "coder", AsyncMock(), display_name="Coder")

        participants = b.get_room_participants()

        assert participants
        assert participants[0]["peer_id"] == "peer-1"
        assert participants[0]["persona"] == "coder"

    @pytest.mark.asyncio
    async def test_get_room_participants_filters_environment(self, room_settings):
        b = Broker(settings=room_settings)
        assert b._room_bridge is not None
        await b._room_bridge.register(
            "cluster-peer",
            "k8s",
            AsyncMock(),
            environment_id="cluster-a",
        )
        await b._room_bridge.register(
            "host-peer",
            "inbox",
            AsyncMock(),
            environment_id="host-mail",
        )

        participants = b.get_room_participants(environment_id="cluster-a")

        assert [participant["peer_id"] for participant in participants] == ["cluster-peer"]

    @pytest.mark.asyncio
    async def test_room_presence_events_publish_to_existing_sleipnir_bus(self, room_settings):
        bus = InProcessBus()
        captured = []

        async def handler(event):
            captured.append(event)

        await bus.subscribe(["participant.*"], handler)
        b = Broker(settings=room_settings, sleipnir_publisher=bus)
        assert b._room_bridge is not None

        await b._room_bridge.register(
            "valkyrie-1",
            "K8s Valkyrie",
            AsyncMock(),
            environment_id="cluster-a",
            participant_kind="valkyrie",
            capabilities=["k8s.inspect_pod"],
        )
        await bus.flush()

        assert [event.event_type for event in captured] == [event_registry.PARTICIPANT_JOINED]
        assert captured[0].payload["environment_id"] == "cluster-a"
        assert captured[0].payload["participant_type"] == "valkyrie"

    @pytest.mark.asyncio
    async def test_room_participants_api_handler_passes_environment_filter(self, monkeypatch):
        get_participants = MagicMock(return_value=[{"peer_id": "cluster-peer"}])
        monkeypatch.setattr(broker, "get_room_participants", get_participants)

        response = await get_room_participants(environment_id="cluster-a")

        assert response == {"participants": [{"peer_id": "cluster-peer"}]}
        get_participants.assert_called_once_with(environment_id="cluster-a")

    def test_get_communication_routes_returns_channel_routes(self, room_settings):
        b = Broker(settings=room_settings)
        mock_channel = MagicMock()
        mock_channel.channel_type = "telegram"
        mock_channel.communication_route.return_value = {
            "platform": "telegram",
            "conversation_id": "chat-1",
            "thread_id": "5",
            "mode": "room",
            "metadata": {"topic_mode": "topic_per_session"},
        }
        b._channels.add(mock_channel)

        routes = b.get_communication_routes()

        assert routes == [
            {
                "platform": "telegram",
                "conversation_id": "chat-1",
                "thread_id": "5",
                "mode": "room",
                "metadata": {"topic_mode": "topic_per_session"},
            }
        ]

    # -----------------------------------------------------------------------
    # room_state on browser connect
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_websocket_sends_room_state_when_room_enabled(self, room_settings):
        b = Broker(settings=room_settings)
        mock_transport = AsyncMock()
        mock_transport.is_alive = True
        mock_transport.capabilities = TransportCapabilities()
        b._transport = mock_transport

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

        await b.handle_websocket(mock_ws)

        calls = [c[0][0] for c in mock_ws.send_json.call_args_list]
        room_state_calls = [c for c in calls if c.get("type") == "room_state"]
        assert len(room_state_calls) == 1
        assert "participants" in room_state_calls[0]

    @pytest.mark.asyncio
    async def test_handle_websocket_no_room_state_when_room_disabled(self, no_room_settings):
        b = Broker(settings=no_room_settings)
        mock_transport = AsyncMock()
        mock_transport.is_alive = True
        mock_transport.capabilities = TransportCapabilities()
        b._transport = mock_transport

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

        await b.handle_websocket(mock_ws)

        calls = [c[0][0] for c in mock_ws.send_json.call_args_list]
        room_state_calls = [c for c in calls if c.get("type") == "room_state"]
        assert len(room_state_calls) == 0

    # -----------------------------------------------------------------------
    # handle_ravn_websocket
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_ravn_websocket_rejects_when_room_disabled(self, no_room_settings):
        b = Broker(settings=no_room_settings)
        mock_ws = AsyncMock()

        await b.handle_ravn_websocket(mock_ws, "agent-1")

        mock_ws.close.assert_awaited_once()
        assert mock_ws.close.call_args[1]["code"] == 1008

    @pytest.mark.asyncio
    async def test_handle_ravn_websocket_registers_on_connect(self, room_settings):
        b = Broker(settings=room_settings)
        mock_bridge = AsyncMock()
        mock_bridge.register = AsyncMock(
            return_value=MagicMock(peer_id="agent-1", persona="agent-1")
        )
        b._room_bridge = mock_bridge

        mock_ws = AsyncMock()
        mock_ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect())

        await b.handle_ravn_websocket(mock_ws, "agent-1")

        mock_ws.accept.assert_awaited_once()
        mock_bridge.register.assert_awaited()
        mock_bridge.unregister.assert_awaited_once_with("agent-1")

    @pytest.mark.asyncio
    async def test_handle_ravn_websocket_forwards_frames(self, room_settings):
        import json as _json

        b = Broker(settings=room_settings)
        mock_bridge = AsyncMock()
        mock_bridge.register = AsyncMock(
            return_value=MagicMock(peer_id="agent-1", persona="agent-1")
        )
        b._room_bridge = mock_bridge
        b._observe_room_peer_event = AsyncMock()

        frame = _json.dumps({"type": "response", "data": "Hello", "metadata": {}}) + "\n"
        mock_ws = AsyncMock()
        mock_ws.receive_text = AsyncMock(side_effect=[frame, WebSocketDisconnect()])

        await b.handle_ravn_websocket(mock_ws, "agent-1")

        mock_bridge.handle_ravn_frame.assert_awaited_once()
        b._observe_room_peer_event.assert_not_awaited()
        call_args = mock_bridge.handle_ravn_frame.call_args[0]
        assert call_args[0] == "agent-1"
        assert call_args[1]["type"] == "response"

    @pytest.mark.asyncio
    async def test_handle_ravn_websocket_skips_invalid_json(self, room_settings):
        b = Broker(settings=room_settings)
        mock_bridge = AsyncMock()
        mock_bridge.register = AsyncMock(
            return_value=MagicMock(peer_id="agent-1", persona="agent-1")
        )
        b._room_bridge = mock_bridge

        mock_ws = AsyncMock()
        mock_ws.receive_text = AsyncMock(side_effect=["not valid json\n", WebSocketDisconnect()])

        await b.handle_ravn_websocket(mock_ws, "agent-1")

        mock_bridge.handle_ravn_frame.assert_not_awaited()
        mock_bridge.unregister.assert_awaited_once_with("agent-1")

    @pytest.mark.asyncio
    async def test_handle_ravn_websocket_unregisters_on_exception(self, room_settings):
        b = Broker(settings=room_settings)
        mock_bridge = AsyncMock()
        mock_bridge.register = AsyncMock(
            return_value=MagicMock(peer_id="agent-1", persona="agent-1")
        )
        b._room_bridge = mock_bridge

        mock_ws = AsyncMock()
        mock_ws.receive_text = AsyncMock(side_effect=RuntimeError("unexpected"))

        await b.handle_ravn_websocket(mock_ws, "agent-1")

        mock_bridge.unregister.assert_awaited_once_with("agent-1")
