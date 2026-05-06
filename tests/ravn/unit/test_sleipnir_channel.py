"""Unit tests for SleipnirChannel, CompositeChannel, and SleipnirEnvelope."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.adapters.channels.composite import CompositeChannel
from ravn.adapters.channels.sleipnir import SleipnirChannel, _serialise_envelope, _urgency_for
from ravn.config import SleipnirConfig
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.models import SleipnirEnvelope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(
    event_type: RavnEventType = RavnEventType.RESPONSE,
    payload: dict | None = None,
    urgency: float = 0.2,
) -> RavnEvent:
    return RavnEvent(
        type=event_type,
        source="test-agent",
        payload=payload or {"text": "hello"},
        timestamp=datetime.now(UTC),
        urgency=urgency,
        correlation_id="corr-1",
        session_id="sess-1",
        task_id=None,
    )


def _config(**kwargs) -> SleipnirConfig:
    defaults = dict(
        enabled=True,
        amqp_url_env="SLEIPNIR_AMQP_URL",
        exchange="ravn.events",
        agent_id="test-host",
        reconnect_delay_s=5.0,
        publish_timeout_s=2.0,
    )
    defaults.update(kwargs)
    return SleipnirConfig(**defaults)


# ---------------------------------------------------------------------------
# Urgency mapping
# ---------------------------------------------------------------------------


class TestUrgencyMapping:
    def test_thought_urgency(self) -> None:
        assert _urgency_for(_event(RavnEventType.THOUGHT)) == 0.1

    def test_tool_start_urgency(self) -> None:
        assert _urgency_for(_event(RavnEventType.TOOL_START)) == 0.1

    def test_tool_result_urgency(self) -> None:
        assert _urgency_for(_event(RavnEventType.TOOL_RESULT)) == 0.1

    def test_response_urgency(self) -> None:
        assert _urgency_for(_event(RavnEventType.RESPONSE)) == 0.2

    def test_error_urgency(self) -> None:
        assert _urgency_for(_event(RavnEventType.ERROR)) == 0.6

    def test_task_complete_success_urgency(self) -> None:
        e = _event(RavnEventType.TASK_COMPLETE, payload={"success": True})
        assert _urgency_for(e) == 0.2

    def test_task_complete_failure_urgency(self) -> None:
        e = _event(RavnEventType.TASK_COMPLETE, payload={"success": False})
        assert _urgency_for(e) == 0.7

    def test_task_complete_defaults_to_success(self) -> None:
        # No 'success' key → treated as success
        e = _event(RavnEventType.TASK_COMPLETE, payload={})
        assert _urgency_for(e) == 0.2


# ---------------------------------------------------------------------------
# SleipnirEnvelope construction
# ---------------------------------------------------------------------------


class TestSleipnirEnvelopeConstruction:
    def test_envelope_fields(self) -> None:
        event = _event()
        envelope = SleipnirEnvelope(
            event=event,
            source_agent="my-agent",
            session_id="sess-42",
            task_id="task-7",
            urgency=0.2,
            correlation_id="corr-1",
            published_at=datetime.now(UTC),
        )
        assert envelope.event is event
        assert envelope.source_agent == "my-agent"
        assert envelope.session_id == "sess-42"
        assert envelope.task_id == "task-7"
        assert envelope.urgency == 0.2

    def test_task_id_none(self) -> None:
        event = _event()
        envelope = SleipnirEnvelope(
            event=event,
            source_agent="agent",
            session_id="sess",
            task_id=None,
            urgency=0.1,
            correlation_id="corr",
            published_at=datetime.now(UTC),
        )
        assert envelope.task_id is None


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------


class TestSerialiseEnvelope:
    def test_serialises_to_bytes(self) -> None:
        import json

        event = _event()
        envelope = SleipnirEnvelope(
            event=event,
            source_agent="agent",
            session_id="sess",
            task_id=None,
            urgency=0.2,
            correlation_id="corr",
            published_at=datetime.now(UTC),
        )
        raw = _serialise_envelope(envelope)
        assert isinstance(raw, bytes)
        data = json.loads(raw)
        assert data["source_agent"] == "agent"
        assert data["session_id"] == "sess"
        assert data["task_id"] is None
        assert data["urgency"] == 0.2
        assert data["event"]["type"] == "response"

    def test_task_id_propagated(self) -> None:
        import json

        event = _event()
        envelope = SleipnirEnvelope(
            event=event,
            source_agent="agent",
            session_id="sess",
            task_id="task-99",
            urgency=0.2,
            correlation_id="corr",
            published_at=datetime.now(UTC),
        )
        data = json.loads(_serialise_envelope(envelope))
        assert data["task_id"] == "task-99"
        assert data["event"]["task_id"] is None  # from the underlying event


# ---------------------------------------------------------------------------
# SleipnirChannel — publish failure handling
# ---------------------------------------------------------------------------


class TestSleipnirChannelPublishFailure:
    @pytest.mark.asyncio
    async def test_emit_does_not_raise_when_amqp_url_missing(self) -> None:
        """No SLEIPNIR_AMQP_URL → emit silently drops the event."""
        config = _config()
        channel = SleipnirChannel(config, session_id="sess-1")

        with patch.dict("os.environ", {}, clear=False):
            # Remove the env var if present
            import os

            os.environ.pop("SLEIPNIR_AMQP_URL", None)
            # Must not raise
            await channel.emit(_event())

    @pytest.mark.asyncio
    async def test_emit_does_not_raise_when_aio_pika_missing(self) -> None:
        """aio_pika ImportError → emit silently drops the event."""
        import ravn.adapters.channels._rabbitmq_base as base_mod

        config = _config()
        channel = SleipnirChannel(config, session_id="sess-1")

        with patch.dict("os.environ", {"SLEIPNIR_AMQP_URL": "amqp://localhost"}):
            with patch.object(base_mod, "aio_pika", None):
                await channel.emit(_event())

    @pytest.mark.asyncio
    async def test_emit_logs_debug_on_publish_failure(self) -> None:
        """A publish exception is logged at DEBUG and not re-raised."""
        import ravn.adapters.channels._rabbitmq_base as base_mod

        config = _config(reconnect_delay_s=0.0)
        channel = SleipnirChannel(config, session_id="sess-1")

        mock_exchange = AsyncMock()
        mock_exchange.publish.side_effect = RuntimeError("boom")

        # Inject the already-connected exchange
        channel._exchange = mock_exchange
        channel._connection = AsyncMock()
        channel._channel = AsyncMock()

        fake_aio_pika = MagicMock()
        fake_aio_pika.Message = MagicMock(return_value=MagicMock())

        with patch("ravn.adapters.channels._rabbitmq_base.logger") as mock_logger:
            with patch.object(base_mod, "aio_pika", fake_aio_pika):
                await channel.emit(_event())

        mock_logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_emit_invalidates_connection_after_failure(self) -> None:
        """Exchange is cleared after a publish failure to force reconnect."""
        import ravn.adapters.channels._rabbitmq_base as base_mod

        config = _config(reconnect_delay_s=0.0)
        channel = SleipnirChannel(config, session_id="sess-1")

        mock_exchange = AsyncMock()
        mock_exchange.publish.side_effect = RuntimeError("network gone")
        channel._exchange = mock_exchange
        channel._connection = AsyncMock()
        channel._channel = AsyncMock()

        fake_aio_pika = MagicMock()
        fake_aio_pika.Message = MagicMock(return_value=MagicMock())

        with patch.object(base_mod, "aio_pika", fake_aio_pika):
            await channel.emit(_event())

        assert channel._exchange is None

    @pytest.mark.asyncio
    async def test_emit_uses_session_id_from_constructor(self) -> None:
        """SleipnirEnvelope.session_id matches the session passed to constructor."""
        import json

        import ravn.adapters.channels._rabbitmq_base as base_mod

        config = _config(reconnect_delay_s=0.0)
        channel = SleipnirChannel(config, session_id="my-special-session")

        captured: list[bytes] = []

        class FakeMessage:
            def __init__(self, body, **kwargs):
                self.body = body

        mock_exchange = AsyncMock()

        async def fake_publish(message, *, routing_key):
            captured.append(message.body)

        mock_exchange.publish.side_effect = fake_publish
        channel._exchange = mock_exchange
        channel._connection = AsyncMock()
        channel._channel = AsyncMock()

        fake_aio_pika = MagicMock()
        fake_aio_pika.Message = FakeMessage

        with patch.object(base_mod, "aio_pika", fake_aio_pika):
            await channel.emit(_event())

        assert len(captured) == 1
        data = json.loads(captured[0])
        assert data["session_id"] == "my-special-session"

    @pytest.mark.asyncio
    async def test_emit_uses_task_id_from_constructor(self) -> None:
        """SleipnirEnvelope.task_id matches the task_id passed to constructor."""
        import json

        import ravn.adapters.channels._rabbitmq_base as base_mod

        config = _config(reconnect_delay_s=0.0)
        channel = SleipnirChannel(config, session_id="sess", task_id="drive-task-42")

        captured: list[bytes] = []

        class FakeMessage:
            def __init__(self, body, **kwargs):
                self.body = body

        mock_exchange = AsyncMock()

        async def fake_publish(message, *, routing_key):
            captured.append(message.body)

        mock_exchange.publish.side_effect = fake_publish
        channel._exchange = mock_exchange
        channel._connection = AsyncMock()
        channel._channel = AsyncMock()

        fake_aio_pika = MagicMock()
        fake_aio_pika.Message = FakeMessage

        with patch.object(base_mod, "aio_pika", fake_aio_pika):
            await channel.emit(_event())

        assert len(captured) == 1
        data = json.loads(captured[0])
        assert data["task_id"] == "drive-task-42"

    @pytest.mark.asyncio
    async def test_routing_key_format(self) -> None:
        """Routing key uses ravn.<event_type>.<agent_id> format."""
        import ravn.adapters.channels._rabbitmq_base as base_mod

        config = _config(agent_id="my-bot", reconnect_delay_s=0.0)
        channel = SleipnirChannel(config, session_id="sess")

        routing_keys: list[str] = []

        class FakeMessage:
            def __init__(self, body, **kwargs):
                self.body = body

        mock_exchange = AsyncMock()

        async def fake_publish(message, *, routing_key):
            routing_keys.append(routing_key)

        mock_exchange.publish.side_effect = fake_publish
        channel._exchange = mock_exchange
        channel._connection = AsyncMock()
        channel._channel = AsyncMock()

        fake_aio_pika = MagicMock()
        fake_aio_pika.Message = FakeMessage

        event = _event(RavnEventType.THOUGHT)
        with patch.object(base_mod, "aio_pika", fake_aio_pika):
            await channel.emit(event)

        assert routing_keys == ["ravn.thought.my-bot"]

    @pytest.mark.asyncio
    async def test_reconnect_not_attempted_before_delay(self) -> None:
        """A second emit within reconnect_delay_s does not attempt a new connection."""
        import ravn.adapters.channels._rabbitmq_base as base_mod

        config = _config(reconnect_delay_s=999.0)
        channel = SleipnirChannel(config, session_id="sess")

        # Simulate a recent failed connection attempt
        channel._last_connect_attempt = asyncio.get_event_loop().time()

        fake_aio_pika = MagicMock()
        fake_aio_pika.connect_robust = AsyncMock()

        with patch.dict("os.environ", {"SLEIPNIR_AMQP_URL": "amqp://localhost"}):
            with patch.object(base_mod, "aio_pika", fake_aio_pika):
                await channel.emit(_event())
                fake_aio_pika.connect_robust.assert_not_called()


# ---------------------------------------------------------------------------
# CompositeChannel
# ---------------------------------------------------------------------------


class TestCompositeChannel:
    @pytest.mark.asyncio
    async def test_broadcasts_to_all_channels(self) -> None:
        ch1 = MagicMock()
        ch1.emit = AsyncMock()
        ch2 = MagicMock()
        ch2.emit = AsyncMock()
        composite = CompositeChannel([ch1, ch2])
        event = _event()

        await composite.emit(event)

        ch1.emit.assert_awaited_once_with(event)
        ch2.emit.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_empty_channels_list(self) -> None:
        composite = CompositeChannel([])
        # Must not raise
        await composite.emit(_event())

    @pytest.mark.asyncio
    async def test_preserves_event_across_channels(self) -> None:
        received: list[RavnEvent] = []

        class _Capture:
            async def emit(self, event: RavnEvent) -> None:
                received.append(event)

        composite = CompositeChannel([_Capture(), _Capture()])
        event = _event(RavnEventType.ERROR)
        await composite.emit(event)

        assert len(received) == 2
        assert all(e is event for e in received)

    @pytest.mark.asyncio
    async def test_single_channel(self) -> None:
        ch = MagicMock()
        ch.emit = AsyncMock()
        composite = CompositeChannel([ch])
        event = _event()

        await composite.emit(event)

        ch.emit.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_exception_in_one_channel_does_not_block_others(self) -> None:
        """Channels that raise are silently skipped; remaining channels still receive."""
        bad_ch = MagicMock()
        bad_ch.emit = AsyncMock(side_effect=RuntimeError("boom"))
        good_ch = MagicMock()
        good_ch.emit = AsyncMock()

        composite = CompositeChannel([bad_ch, good_ch])
        await composite.emit(_event())  # must not raise

        good_ch.emit.assert_awaited_once()


# ---------------------------------------------------------------------------
# SleipnirConfig defaults
# ---------------------------------------------------------------------------


class TestSleipnirConfigDefaults:
    def test_disabled_by_default(self) -> None:
        from ravn.config import Settings

        s = Settings()
        assert s.sleipnir.enabled is False

    def test_default_exchange(self) -> None:
        config = SleipnirConfig()
        assert config.exchange == "ravn.events"

    def test_default_amqp_url_env(self) -> None:
        config = SleipnirConfig()
        assert config.amqp_url_env == "SLEIPNIR_AMQP_URL"

    def test_default_reconnect_delay(self) -> None:
        config = SleipnirConfig()
        assert config.reconnect_delay_s == 5.0

    def test_default_publish_timeout(self) -> None:
        config = SleipnirConfig()
        assert config.publish_timeout_s == 2.0
