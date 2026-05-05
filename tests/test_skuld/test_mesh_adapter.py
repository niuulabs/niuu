"""Tests for Skuld mesh adapter (NIU-612).

Covers:
- SkuldMeshAdapter lifecycle (start/stop)
- Config parsing (mesh.enabled true/false)
- Work request handling (prompt → CLI → result)
- Outcome extraction and response publishing
- Concurrency safety (execute lock)
- Integration with InProcessBus round-trip
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.domain.events import RavnEvent, RavnEventType
from skuld.config import MeshConfig, SkuldSettings
from skuld.mesh_adapter import SkuldMeshAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mesh_config():
    return MeshConfig(
        enabled=True,
        peer_id="skuld-test-01",
        capabilities=["coding", "git"],
        persona="coder",
        transport="in_process",
        consumes_event_types=["code.requested"],
        rpc_timeout_s=5.0,
    )


@pytest.fixture
def disabled_mesh_config():
    return MeshConfig(enabled=False)


@pytest.fixture
def mock_mesh():
    mesh = MagicMock()
    mesh.start = AsyncMock()
    mesh.stop = AsyncMock()
    mesh.publish = AsyncMock()
    mesh.subscribe = AsyncMock()
    mesh.unsubscribe = AsyncMock()
    mesh.set_rpc_handler = MagicMock()
    return mesh


@pytest.fixture
def mock_transport():
    """Mock CLITransport exposing public event_callback property."""
    transport = MagicMock()
    transport.send_message = AsyncMock()
    transport.is_alive = True

    _callback_holder: dict[str, object] = {"cb": None}

    def on_event(callback):
        _callback_holder["cb"] = callback

    transport.on_event = on_event

    # Public property matching CLITransport.event_callback
    type(transport).event_callback = property(lambda self: _callback_holder["cb"])

    # Also expose the internal for fake_send helpers to fire
    type(transport)._event_callback = property(lambda self: _callback_holder["cb"])

    return transport


@pytest.fixture
def adapter(mock_mesh, mock_transport, mesh_config):
    from niuu.mesh.participant import MeshParticipant

    participant = MeshParticipant(mesh=mock_mesh, discovery=None, peer_id=mesh_config.peer_id)
    return SkuldMeshAdapter(
        participant=participant,
        transport=mock_transport,
        config=mesh_config,
        session_id="test-session-42",
    )


# ---------------------------------------------------------------------------
# Config parsing tests
# ---------------------------------------------------------------------------


class TestMeshConfig:
    """Test mesh configuration parsing."""

    @pytest.fixture(autouse=True)
    def _no_yaml_config(self, monkeypatch):
        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])

    def test_mesh_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("SKULD__MESH__ENABLED", raising=False)
        s = SkuldSettings()
        assert s.mesh.enabled is False
        assert s.mesh.peer_id == ""
        assert s.mesh.persona == "coder"

    def test_mesh_enabled_via_constructor(self):
        s = SkuldSettings(mesh={"enabled": True, "peer_id": "my-skuld"})
        assert s.mesh.enabled is True
        assert s.mesh.peer_id == "my-skuld"

    def test_mesh_default_capabilities(self):
        cfg = MeshConfig()
        assert "coding" in cfg.capabilities
        assert "git" in cfg.capabilities
        assert "terminal" in cfg.capabilities
        assert "file_edit" in cfg.capabilities

    def test_mesh_default_consumes(self):
        cfg = MeshConfig()
        assert cfg.consumes_event_types == ["code.requested"]

    def test_mesh_custom_capabilities(self):
        cfg = MeshConfig(capabilities=["review"])
        assert cfg.capabilities == ["review"]

    def test_mesh_transport_default(self):
        cfg = MeshConfig()
        assert cfg.transport == "nng"

    def test_mesh_rpc_timeout(self):
        cfg = MeshConfig(rpc_timeout_s=30.0)
        assert cfg.rpc_timeout_s == 30.0

    def test_mesh_default_work_timeout(self):
        cfg = MeshConfig()
        assert cfg.default_work_timeout_s == 120.0

    def test_mesh_custom_work_timeout(self):
        cfg = MeshConfig(default_work_timeout_s=300.0)
        assert cfg.default_work_timeout_s == 300.0

    def test_mesh_default_response_urgency(self):
        cfg = MeshConfig()
        assert cfg.default_response_urgency == 0.3

    def test_mesh_custom_response_urgency(self):
        cfg = MeshConfig(default_response_urgency=0.7)
        assert cfg.default_response_urgency == 0.7


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestSkuldMeshAdapterLifecycle:
    """Test start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_starts_mesh_and_subscribes(self, adapter, mock_mesh):
        await adapter.start()

        mock_mesh.start.assert_awaited_once()
        mock_mesh.subscribe.assert_awaited_once_with(
            "code.requested", adapter._handle_outcome_event
        )
        assert adapter.is_running is True

    @pytest.mark.asyncio
    async def test_start_registers_rpc_handler(self, adapter, mock_mesh):
        await adapter.start()

        mock_mesh.set_rpc_handler.assert_called_once_with(adapter._handle_rpc)

    @pytest.mark.asyncio
    async def test_start_with_discovery(self, mock_mesh, mock_transport, mesh_config):
        from niuu.mesh.participant import MeshParticipant

        discovery = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        participant = MeshParticipant(mesh=mock_mesh, discovery=discovery, peer_id="s1")
        adapter = SkuldMeshAdapter(
            participant=participant,
            transport=mock_transport,
            config=mesh_config,
            session_id="s1",
        )
        await adapter.start()

        discovery.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_unsubscribes_and_stops(self, adapter, mock_mesh):
        await adapter.start()
        await adapter.stop()

        mock_mesh.unsubscribe.assert_awaited_once_with("code.requested")
        mock_mesh.stop.assert_awaited_once()
        assert adapter.is_running is False

    @pytest.mark.asyncio
    async def test_stop_with_discovery(self, mock_mesh, mock_transport, mesh_config):
        from niuu.mesh.participant import MeshParticipant

        discovery = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        participant = MeshParticipant(mesh=mock_mesh, discovery=discovery, peer_id="s1")
        adapter = SkuldMeshAdapter(
            participant=participant,
            transport=mock_transport,
            config=mesh_config,
            session_id="s1",
        )
        await adapter.start()
        await adapter.stop()

        discovery.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self, adapter, mock_mesh):
        await adapter.start()
        await adapter.start()

        assert mock_mesh.start.await_count == 1

    @pytest.mark.asyncio
    async def test_stop_when_not_started_is_noop(self, adapter, mock_mesh):
        await adapter.stop()
        mock_mesh.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_peer_id_from_config(self, adapter):
        assert adapter.peer_id == "skuld-test-01"

    @pytest.mark.asyncio
    async def test_peer_id_defaults_to_hostname(self, mock_mesh, mock_transport):
        import socket

        from niuu.mesh.participant import MeshParticipant

        cfg = MeshConfig(enabled=True, peer_id="")
        participant = MeshParticipant(mesh=mock_mesh, discovery=None, peer_id="")
        adapter = SkuldMeshAdapter(
            participant=participant,
            transport=mock_transport,
            config=cfg,
            session_id="s1",
        )
        assert adapter.peer_id == socket.gethostname()

    @pytest.mark.asyncio
    async def test_publish_forwards_event_to_mesh(self, adapter, mock_mesh):
        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source="skuld:test",
            payload={"event_type": "code.requested"},
            timestamp=datetime.now(UTC),
            urgency=0.5,
            correlation_id="corr-1",
            session_id="session-1",
        )

        await adapter.publish(event, "code.requested")

        mock_mesh.publish.assert_awaited_once_with(event, topic="code.requested")

    @pytest.mark.asyncio
    async def test_multiple_event_type_subscriptions(self, mock_mesh, mock_transport):
        from niuu.mesh.participant import MeshParticipant

        cfg = MeshConfig(
            enabled=True,
            peer_id="s1",
            consumes_event_types=["code.requested", "review.requested"],
        )
        participant = MeshParticipant(mesh=mock_mesh, discovery=None, peer_id="s1")
        adapter = SkuldMeshAdapter(
            participant=participant,
            transport=mock_transport,
            config=cfg,
            session_id="s1",
        )
        await adapter.start()

        assert mock_mesh.subscribe.await_count == 2
        await adapter.stop()
        assert mock_mesh.unsubscribe.await_count == 2


# ---------------------------------------------------------------------------
# Work request (RPC) tests
# ---------------------------------------------------------------------------


class TestWorkRequestHandling:
    """Test work_request RPC handling."""

    @pytest.mark.asyncio
    async def test_work_request_sends_prompt_to_cli(self, adapter, mock_transport):
        await adapter.start()

        # Simulate CLI returning a result event
        async def fake_send(content):
            cb = mock_transport._event_callback
            if cb:
                await cb({"type": "result", "result": "Task completed successfully"})

        mock_transport.send_message = fake_send

        result = await adapter._handle_work_request(
            {
                "prompt": "Fix the bug in main.py",
                "event_type": "code.requested",
                "request_id": "req-001",
                "timeout_s": 5.0,
            }
        )

        assert result["status"] == "complete"
        assert result["request_id"] == "req-001"
        assert result["output"] == "Task completed successfully"
        assert result["event_type"] == "code.requested"

    @pytest.mark.asyncio
    async def test_work_request_extracts_outcome(self, adapter, mock_transport):
        await adapter.start()

        response_text = (
            "I fixed the bug.\n\n---outcome---\nverdict: approved\nfiles_changed: 1\n---end---"
        )

        async def fake_send(content):
            cb = mock_transport._event_callback
            if cb:
                await cb({"type": "result", "result": response_text})

        mock_transport.send_message = fake_send

        result = await adapter._handle_work_request(
            {
                "prompt": "Fix bug",
                "event_type": "code.requested",
                "request_id": "req-002",
            }
        )

        assert result["status"] == "complete"
        assert "outcome" in result
        assert result["outcome"]["fields"]["verdict"] == "approved"
        assert result["outcome"]["valid"] is True

    @pytest.mark.asyncio
    async def test_work_request_empty_prompt_returns_error(self, adapter):
        await adapter.start()

        result = await adapter._handle_work_request(
            {
                "prompt": "",
                "request_id": "req-003",
            }
        )

        assert result["status"] == "error"
        assert result["error"] == "empty prompt"

    @pytest.mark.asyncio
    async def test_work_request_timeout(self, adapter, mock_transport):
        await adapter.start()

        async def slow_send(content):
            # Never fires a result event
            await asyncio.sleep(10)

        mock_transport.send_message = slow_send

        result = await adapter._handle_work_request(
            {
                "prompt": "Do something slow",
                "event_type": "code.requested",
                "request_id": "req-004",
                "timeout_s": 0.1,
            }
        )

        assert result["status"] == "timeout"
        assert result["request_id"] == "req-004"

    @pytest.mark.asyncio
    async def test_work_request_uses_config_default_timeout(self, mock_mesh, mock_transport):
        """When no timeout_s in message, use config.default_work_timeout_s."""
        from niuu.mesh.participant import MeshParticipant

        cfg = MeshConfig(
            enabled=True,
            peer_id="s1",
            default_work_timeout_s=0.05,
        )
        participant = MeshParticipant(mesh=mock_mesh, discovery=None, peer_id="s1")
        adapter = SkuldMeshAdapter(
            participant=participant,
            transport=mock_transport,
            config=cfg,
            session_id="s1",
        )
        await adapter.start()

        async def slow_send(content):
            await asyncio.sleep(10)

        mock_transport.send_message = slow_send

        result = await adapter._handle_work_request(
            {
                "prompt": "slow",
                "request_id": "req-cfg-timeout",
                # no timeout_s — should use config default (0.05s)
            }
        )

        assert result["status"] == "timeout"

    @pytest.mark.asyncio
    async def test_unknown_rpc_type_returns_error(self, adapter):
        await adapter.start()

        result = await adapter._handle_rpc({"type": "unknown_type"})
        assert result["error"] == "unknown_message_type"

    @pytest.mark.asyncio
    async def test_work_request_restores_callback(self, adapter, mock_transport):
        """Verify original event callback is restored after work request."""
        await adapter.start()

        original_cb = AsyncMock()
        adapter._transport.on_event(original_cb)

        async def fake_send(content):
            cb = mock_transport._event_callback
            if cb:
                await cb({"type": "result", "result": "done"})

        mock_transport.send_message = fake_send

        await adapter._handle_work_request(
            {
                "prompt": "test",
                "request_id": "req-005",
            }
        )

        # The original callback should have been restored
        assert mock_transport.event_callback is original_cb

    @pytest.mark.asyncio
    async def test_work_request_exception_returns_error(self, adapter, mock_transport):
        """Work request that raises should return error status."""
        await adapter.start()

        async def exploding_send(content):
            raise RuntimeError("CLI crashed")

        mock_transport.send_message = exploding_send

        result = await adapter._handle_work_request(
            {
                "prompt": "trigger error",
                "event_type": "code.requested",
                "request_id": "req-err",
            }
        )

        assert result["status"] == "error"
        assert "CLI crashed" in result["error"]

    @pytest.mark.asyncio
    async def test_work_request_collects_assistant_text(self, adapter, mock_transport):
        """When result has no text, assistant content is used."""
        await adapter.start()

        async def fake_send(content):
            cb = mock_transport._event_callback
            if cb:
                await cb(
                    {
                        "type": "assistant",
                        "message": {"content": "Partial response"},
                    }
                )
                # Result with empty text
                await cb({"type": "result", "result": ""})

        mock_transport.send_message = fake_send

        result = await adapter._handle_work_request(
            {
                "prompt": "test",
                "request_id": "req-assist",
            }
        )

        assert result["status"] == "complete"
        # Result text is from assistant because result was empty — assistant text kept
        assert result["output"] == "Partial response"

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_futures(self, adapter, mock_transport):
        """Pending response futures should be cancelled on stop."""
        await adapter.start()

        # Manually add a pending future
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        adapter._pending_responses["test-req"] = fut

        await adapter.stop()

        assert fut.cancelled()
        assert len(adapter._pending_responses) == 0


# ---------------------------------------------------------------------------
# Concurrency safety tests
# ---------------------------------------------------------------------------


class TestConcurrencySafety:
    """Test that _execute_prompt serializes concurrent calls."""

    @pytest.mark.asyncio
    async def test_execute_lock_serializes_calls(self, adapter, mock_transport):
        """Two overlapping work_requests should not corrupt the callback chain."""
        await adapter.start()

        call_order: list[str] = []

        async def sequenced_send(content):
            call_order.append(f"send:{content}")
            # Small delay to simulate real work
            await asyncio.sleep(0.01)
            cb = mock_transport._event_callback
            if cb:
                await cb({"type": "result", "result": f"done:{content}"})

        mock_transport.send_message = sequenced_send

        # Launch two concurrent work requests
        r1 = asyncio.create_task(
            adapter._handle_work_request({"prompt": "first", "request_id": "r1", "timeout_s": 5.0})
        )
        r2 = asyncio.create_task(
            adapter._handle_work_request({"prompt": "second", "request_id": "r2", "timeout_s": 5.0})
        )

        results = await asyncio.gather(r1, r2)

        # Both should complete successfully (not corrupt)
        statuses = {r["status"] for r in results}
        assert statuses == {"complete"}

        # The lock ensures sends are serialized (not interleaved)
        assert call_order == ["send:first", "send:second"]


# ---------------------------------------------------------------------------
# Outcome event subscription tests
# ---------------------------------------------------------------------------


class TestOutcomeEventHandling:
    """Test outcome event handling via mesh subscriptions."""

    @pytest.mark.asyncio
    async def test_non_outcome_event_ignored(self, adapter):
        event = RavnEvent(
            type=RavnEventType.THOUGHT,
            source="other-ravn",
            payload={"text": "thinking..."},
            timestamp=datetime.now(UTC),
            urgency=0.1,
            correlation_id="c1",
            session_id="s1",
        )
        # Should not raise
        await adapter._handle_outcome_event(event)

    @pytest.mark.asyncio
    async def test_outcome_without_prompt_ignored(self, adapter):
        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source="other-ravn",
            payload={"event_type": "code.requested"},
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="c1",
            session_id="s1",
        )
        await adapter._handle_outcome_event(event)
        # No crash, no publish
        adapter._mesh.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_outcome_with_prompt_executes_and_publishes(
        self, adapter, mock_transport, mock_mesh
    ):
        await adapter.start()

        async def fake_send(content):
            cb = mock_transport._event_callback
            if cb:
                await cb({"type": "result", "result": "Fixed the code"})

        mock_transport.send_message = fake_send

        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source="ravn-coder",
            payload={
                "event_type": "code.requested",
                "prompt": "Fix the authentication bug",
            },
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="corr-123",
            session_id="s1",
        )

        await adapter._handle_outcome_event(event)

        # Verify a response was published back
        mock_mesh.publish.assert_awaited_once()
        call_args = mock_mesh.publish.call_args
        published_event = call_args[0][0]
        assert published_event.type == RavnEventType.OUTCOME
        assert published_event.source == "skuld-test-01"
        assert published_event.payload["persona"] == "coder"
        assert published_event.payload["output"] == "Fixed the code"
        assert call_args[1]["topic"] == "coder.completed"

    @pytest.mark.asyncio
    async def test_outcome_uses_config_response_urgency(self, mock_mesh, mock_transport):
        """Published outcome event uses config.default_response_urgency."""
        from niuu.mesh.participant import MeshParticipant

        cfg = MeshConfig(
            enabled=True,
            peer_id="urg-test",
            default_response_urgency=0.7,
        )
        participant = MeshParticipant(mesh=mock_mesh, discovery=None, peer_id="urg-test")
        adapter = SkuldMeshAdapter(
            participant=participant,
            transport=mock_transport,
            config=cfg,
            session_id="s1",
        )
        await adapter.start()

        async def fake_send(content):
            cb = mock_transport._event_callback
            if cb:
                await cb({"type": "result", "result": "done"})

        mock_transport.send_message = fake_send

        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source="peer",
            payload={"event_type": "code.requested", "prompt": "test"},
            timestamp=datetime.now(UTC),
            urgency=0.1,
            correlation_id="c1",
            session_id="s1",
        )

        await adapter._handle_outcome_event(event)

        published = mock_mesh.publish.call_args[0][0]
        assert published.urgency == 0.7

    @pytest.mark.asyncio
    async def test_outcome_execute_failure_publishes_error(
        self, adapter, mock_transport, mock_mesh
    ):
        """When _execute_prompt raises, the error is published back."""
        await adapter.start()

        async def exploding_send(content):
            raise RuntimeError("CLI crashed")

        mock_transport.send_message = exploding_send

        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source="ravn-coder",
            payload={
                "event_type": "code.requested",
                "prompt": "Do something that fails",
            },
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="corr-err",
            session_id="s1",
        )

        await adapter._handle_outcome_event(event)

        # Should still publish a response (with error text)
        mock_mesh.publish.assert_awaited_once()
        call_args = mock_mesh.publish.call_args
        published_event = call_args[0][0]
        assert "Error:" in published_event.payload["output"]


# ---------------------------------------------------------------------------
# Integration: InProcessBus round-trip
# ---------------------------------------------------------------------------


class TestMeshRoundTrip:
    """Integration test using InProcessBus — no real CLI needed."""

    @pytest.mark.asyncio
    async def test_rpc_round_trip_with_in_process_bus(self):
        """Publish work_request via mesh RPC, verify response."""
        from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
        from sleipnir.adapters.in_process import InProcessBus

        bus = InProcessBus()
        mesh = SleipnirMeshAdapter(
            publisher=bus,
            subscriber=bus,
            own_peer_id="skuld-integration",
            rpc_timeout_s=5.0,
        )

        # Mock transport that echoes back
        transport = MagicMock()
        _cb_holder: dict[str, object] = {"cb": None}

        def on_event(cb):
            _cb_holder["cb"] = cb

        transport.on_event = on_event
        type(transport).event_callback = property(lambda self: _cb_holder["cb"])
        type(transport)._event_callback = property(lambda self: _cb_holder["cb"])

        async def fake_send(content):
            cb = _cb_holder["cb"]
            if cb:
                await cb({"type": "result", "result": f"Echo: {content}"})

        transport.send_message = fake_send

        config = MeshConfig(
            enabled=True,
            peer_id="skuld-integration",
            consumes_event_types=["code.requested"],
        )

        from niuu.mesh.participant import MeshParticipant

        participant = MeshParticipant(mesh=mesh, discovery=None, peer_id="skuld-integration")
        adapter = SkuldMeshAdapter(
            participant=participant,
            transport=transport,
            config=config,
            session_id="int-session",
        )

        await adapter.start()

        # Directly call the RPC handler
        result = await adapter._handle_work_request(
            {
                "prompt": "Hello mesh!",
                "event_type": "code.requested",
                "request_id": "int-001",
            }
        )

        assert result["status"] == "complete"
        assert result["output"] == "Echo: Hello mesh!"

        await adapter.stop()

    @pytest.mark.asyncio
    async def test_publish_subscribe_round_trip(self):
        """Subscribe to topic, publish event, verify handler fires."""
        from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
        from sleipnir.adapters.in_process import InProcessBus

        bus = InProcessBus()
        mesh = SleipnirMeshAdapter(
            publisher=bus,
            subscriber=bus,
            own_peer_id="skuld-pubsub",
            rpc_timeout_s=5.0,
        )

        received: list[RavnEvent] = []

        async def handler(event: RavnEvent) -> None:
            received.append(event)

        await mesh.start()
        await mesh.subscribe("test.topic", handler)

        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source="test-source",
            payload={"event_type": "test.topic", "data": "hello"},
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="c1",
            session_id="s1",
        )

        await mesh.publish(event, topic="test.topic")
        await bus.flush()

        # Give async handler a chance to run
        await asyncio.sleep(0.1)

        assert len(received) >= 1
        assert received[0].payload["data"] == "hello"

        await mesh.stop()


# ---------------------------------------------------------------------------
# Shared niuu.mesh builder tests
# ---------------------------------------------------------------------------


class TestNiuuMeshBuilder:
    """Test shared mesh builder in niuu.mesh."""

    def test_resolve_peer_id_with_value(self):
        from niuu.mesh import resolve_peer_id

        assert resolve_peer_id("my-peer") == "my-peer"

    def test_resolve_peer_id_empty_falls_back_to_hostname(self):
        import socket

        from niuu.mesh import resolve_peer_id

        assert resolve_peer_id("") == socket.gethostname()

    def test_build_in_process_mesh(self):
        from niuu.mesh import build_in_process_mesh

        mesh = build_in_process_mesh("test-peer", rpc_timeout_s=5.0)
        assert mesh is not None

    def test_build_mesh_from_adapters_list_empty(self):
        from niuu.mesh import build_mesh_from_adapters_list

        result = build_mesh_from_adapters_list(
            adapters=[],
            own_peer_id="test",
            rpc_timeout_s=5.0,
        )
        assert result is None

    def test_build_mesh_from_adapters_list_bad_import(self):
        from niuu.mesh import build_mesh_from_adapters_list

        result = build_mesh_from_adapters_list(
            adapters=[{"adapter": "nonexistent.module.Class"}],
            own_peer_id="test",
            rpc_timeout_s=5.0,
        )
        assert result is None

    def test_build_mesh_from_adapters_list_missing_adapter_key(self):
        from niuu.mesh import build_mesh_from_adapters_list

        result = build_mesh_from_adapters_list(
            adapters=[{"not_adapter": "foo"}],
            own_peer_id="test",
            rpc_timeout_s=5.0,
        )
        assert result is None

    def test_mesh_aliases_resolve(self):
        from niuu.mesh import MESH_ALIASES

        assert "sleipnir" in MESH_ALIASES
        assert "webhook" in MESH_ALIASES
        assert "SleipnirMeshAdapter" in MESH_ALIASES["sleipnir"]


# ---------------------------------------------------------------------------
# Broker integration: mesh disabled → no adapter
# ---------------------------------------------------------------------------


class TestBrokerMeshIntegration:
    """Test mesh adapter integration in Broker."""

    @pytest.fixture(autouse=True)
    def _no_yaml_config(self, monkeypatch):
        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])

    def test_mesh_disabled_no_adapter_created(self, tmp_path):
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            mesh={"enabled": False},
        )
        b = Broker(settings=settings)
        assert b._mesh_adapter is None

    def test_mesh_enabled_field_initially_none(self, tmp_path):
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            mesh={"enabled": True, "peer_id": "test-skuld"},
        )
        b = Broker(settings=settings)
        # Before startup, mesh_adapter is None
        assert b._mesh_adapter is None

    @pytest.mark.asyncio
    async def test_start_mesh_adapter_integrates(self, tmp_path):
        """Test _start_mesh_adapter with in_process transport."""
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            transport="subprocess",
            mesh={"enabled": True, "peer_id": "test-skuld", "transport": "in_process"},
        )
        b = Broker(settings=settings)

        transport = MagicMock()
        _cb_holder: dict[str, object] = {"cb": None}

        def on_event(cb):
            _cb_holder["cb"] = cb

        transport.on_event = on_event
        type(transport).event_callback = property(lambda self: _cb_holder["cb"])
        b._transport = transport

        await b._start_mesh_adapter()

        assert b._mesh_adapter is not None
        assert b._mesh_adapter.is_running is True

        await b._mesh_adapter.stop()

    @pytest.mark.asyncio
    async def test_start_mesh_adapter_nng_builds_sleipnir(self, tmp_path):
        """_start_mesh_adapter with nng transport builds SleipnirMeshAdapter (NIU-634)."""
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            transport="subprocess",
            mesh={"enabled": True, "peer_id": "test-peer", "transport": "nng"},
        )
        b = Broker(settings=settings)
        b._transport = MagicMock()

        mock_nng = MagicMock()
        mock_mesh = MagicMock()
        mock_mesh.start = AsyncMock()
        mock_mesh.stop = AsyncMock()
        mock_mesh.subscribe = AsyncMock()
        mock_mesh.unsubscribe = AsyncMock()

        with (
            patch(
                "niuu.mesh.transport_builder.build_nng_transport",
                return_value=mock_nng,
            ),
            patch(
                "ravn.adapters.mesh.sleipnir_mesh.SleipnirMeshAdapter",
                return_value=mock_mesh,
            ),
            patch("skuld.broker.build_discovery_adapters", return_value=None),
            patch("niuu.mesh.cluster.read_cluster_pub_addresses", return_value=[]),
        ):
            await b._start_mesh_adapter()

        assert b._mesh_adapter is not None
        assert b._mesh_adapter.is_running is True
        await b._mesh_adapter.stop()

    @pytest.mark.asyncio
    async def test_start_mesh_adapter_nng_import_error_falls_back(self, tmp_path):
        """_start_mesh_adapter falls back to in-process when nng raises ImportError."""
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            transport="subprocess",
            mesh={"enabled": True, "peer_id": "test-peer", "transport": "nng"},
        )
        b = Broker(settings=settings)
        b._transport = MagicMock()

        with (
            patch(
                "niuu.mesh.transport_builder.build_nng_transport",
                side_effect=ImportError("nng not available"),
            ),
            patch("skuld.broker.build_discovery_adapters", return_value=None),
        ):
            await b._start_mesh_adapter()

        assert b._mesh_adapter is not None
        assert b._mesh_adapter.is_running is True
        await b._mesh_adapter.stop()
