"""Tests for EventPublisherPort, NoOpEventPublisher, and RabbitMQEventPublisher."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.adapters.events.noop_publisher import NoOpEventPublisher
from ravn.adapters.events.rabbitmq_publisher import RabbitMQEventPublisher
from ravn.config import InitiativeConfig, Settings
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.models import AgentTask, OutputMode
from ravn.drive_loop import DriveLoop
from ravn.ports.event_publisher import EventPublisherPort

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: RavnEventType = RavnEventType.TASK_STARTED) -> RavnEvent:
    return RavnEvent(
        type=event_type,
        source="drive_loop",
        payload={"task_id": "task_abc", "heartbeat": True},
        timestamp=datetime.now(UTC),
        urgency=0.0,
        correlation_id="heartbeat",
        session_id="daemon",
        task_id=None,
    )


def _make_sleipnir_config(**kwargs) -> MagicMock:
    config = MagicMock()
    config.agent_id = kwargs.get("agent_id", "test-agent")
    config.amqp_url_env = kwargs.get("amqp_url_env", "SLEIPNIR_AMQP_URL")
    config.exchange = kwargs.get("exchange", "ravn.events")
    config.reconnect_delay_s = kwargs.get("reconnect_delay_s", 5.0)
    config.publish_timeout_s = kwargs.get("publish_timeout_s", 2.0)
    return config


# ---------------------------------------------------------------------------
# EventPublisherPort — ABC enforcement
# ---------------------------------------------------------------------------


def test_event_publisher_port_is_abstract() -> None:
    """EventPublisherPort cannot be instantiated directly."""
    with pytest.raises(TypeError):
        EventPublisherPort()  # type: ignore[abstract]


def test_event_publisher_port_close_is_optional() -> None:
    """close() has a default no-op implementation."""

    class ConcretePublisher(EventPublisherPort):
        async def publish(self, event: RavnEvent) -> None:
            pass

    pub = ConcretePublisher()
    # close() should be callable (it has a default impl)
    assert hasattr(pub, "close")


# ---------------------------------------------------------------------------
# NoOpEventPublisher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_publisher_accepts_any_event() -> None:
    """NoOpEventPublisher.publish() accepts any event without raising."""
    pub = NoOpEventPublisher()
    for event_type in RavnEventType:
        event = _make_event(event_type)
        # Must not raise
        await pub.publish(event)


@pytest.mark.asyncio
async def test_noop_publisher_is_event_publisher_port() -> None:
    pub = NoOpEventPublisher()
    assert isinstance(pub, EventPublisherPort)


@pytest.mark.asyncio
async def test_noop_publisher_close_does_not_raise() -> None:
    pub = NoOpEventPublisher()
    await pub.close()


# ---------------------------------------------------------------------------
# RabbitMQEventPublisher — aio_pika not installed path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rabbitmq_publisher_no_aio_pika_drops_silently() -> None:
    """When aio_pika is not installed, publish() drops events silently."""
    config = _make_sleipnir_config()
    pub = RabbitMQEventPublisher(config)

    with patch.dict("sys.modules", {"aio_pika": None}):
        # Reset cached state so _connect() is called
        pub._exchange = None
        pub._last_connect_attempt = 0.0
        await pub.publish(_make_event())
    # No exception raised


@pytest.mark.asyncio
async def test_rabbitmq_publisher_no_amqp_url_drops_silently() -> None:
    """When AMQP URL env var is not set, publish() drops events silently."""
    config = _make_sleipnir_config(amqp_url_env="NONEXISTENT_AMQP_URL_VAR")
    pub = RabbitMQEventPublisher(config)

    mock_pika = MagicMock()
    mock_pika.ExchangeType = MagicMock()

    with patch.dict("sys.modules", {"aio_pika": mock_pika}):
        import os

        with patch.dict(os.environ, {}, clear=False):
            # Ensure env var not set
            os.environ.pop("NONEXISTENT_AMQP_URL_VAR", None)
            pub._exchange = None
            pub._last_connect_attempt = 0.0
            await pub.publish(_make_event())
    # No exception raised


@pytest.mark.asyncio
async def test_rabbitmq_publisher_is_event_publisher_port() -> None:
    config = _make_sleipnir_config()
    pub = RabbitMQEventPublisher(config)
    assert isinstance(pub, EventPublisherPort)


@pytest.mark.asyncio
async def test_rabbitmq_publisher_uses_system_routing_key() -> None:
    """RabbitMQEventPublisher uses ravn.system.<type>.<agent_id> routing key."""
    config = _make_sleipnir_config(agent_id="my-agent")
    pub = RabbitMQEventPublisher(config)

    mock_exchange = AsyncMock()
    pub._exchange = mock_exchange

    mock_pika = MagicMock()
    mock_message_instance = MagicMock()
    mock_pika.Message.return_value = mock_message_instance

    # Patch the module-level aio_pika reference in the shared base
    with patch("ravn.adapters.channels._rabbitmq_base.aio_pika", mock_pika):
        await pub.publish(_make_event(RavnEventType.TASK_STARTED))

    mock_exchange.publish.assert_called_once()
    call_kwargs = mock_exchange.publish.call_args
    routing_key = call_kwargs[1]["routing_key"]
    assert routing_key == "ravn.system.task_started.my-agent"


@pytest.mark.asyncio
async def test_rabbitmq_publisher_reconnect_delay_respected() -> None:
    """RabbitMQEventPublisher respects reconnect_delay_s between attempts."""
    config = _make_sleipnir_config(reconnect_delay_s=60.0)
    pub = RabbitMQEventPublisher(config)
    pub._last_connect_attempt = asyncio.get_event_loop().time()  # just attempted

    # Should not attempt connection again immediately
    result = await pub._ensure_exchange()
    assert result is None


@pytest.mark.asyncio
async def test_rabbitmq_publisher_close_clears_connection() -> None:
    """close() clears connection state."""
    config = _make_sleipnir_config()
    pub = RabbitMQEventPublisher(config)

    mock_conn = AsyncMock()
    pub._connection = mock_conn
    pub._exchange = MagicMock()
    pub._channel = MagicMock()

    await pub.close()

    assert pub._exchange is None
    assert pub._channel is None
    assert pub._connection is None
    mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# DriveLoop — event_publisher integration
# ---------------------------------------------------------------------------


def _make_task() -> AgentTask:
    hex_ts = hex(int(time.time() * 1000))[2:]
    return AgentTask(
        task_id=f"task_{hex_ts}_0001",
        title="test task",
        initiative_context="do something",
        triggered_by="cron:test",
        output_mode=OutputMode.SILENT,
    )


def _make_drive_loop_with_publisher(
    tmp_path: Path,
    publisher: EventPublisherPort | None = None,
) -> DriveLoop:
    journal = tmp_path / "queue.json"
    config = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=2,
        task_queue_max=10,
        queue_journal_path=str(journal),
        default_output_mode="silent",
    )
    settings = Settings()
    factory = MagicMock(return_value=MagicMock())
    return DriveLoop(
        agent_factory=factory,
        config=config,
        settings=settings,
        event_publisher=publisher,
    )


@pytest.mark.asyncio
async def test_drive_loop_defaults_to_noop_publisher(tmp_path: Path) -> None:
    """DriveLoop uses NoOpEventPublisher when event_publisher is None."""
    loop = _make_drive_loop_with_publisher(tmp_path, publisher=None)
    assert isinstance(loop._event_publisher, NoOpEventPublisher)


@pytest.mark.asyncio
async def test_drive_loop_accepts_custom_publisher(tmp_path: Path) -> None:
    """DriveLoop stores the injected event_publisher."""
    custom_pub = NoOpEventPublisher()
    loop = _make_drive_loop_with_publisher(tmp_path, publisher=custom_pub)
    assert loop._event_publisher is custom_pub


@pytest.mark.asyncio
async def test_drive_loop_run_task_calls_publish_twice(tmp_path: Path) -> None:
    """_run_task() calls event_publisher.publish() for TASK_STARTED and TASK_COMPLETE."""
    mock_publisher = AsyncMock(spec=EventPublisherPort)
    loop = _make_drive_loop_with_publisher(tmp_path, publisher=mock_publisher)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=None)
    loop._agent_factory = MagicMock(return_value=mock_agent)

    # Patch SilentChannel to avoid surface_triggered side effects
    with patch("ravn.drive_loop.SilentChannel") as mock_ch_cls:
        mock_ch = MagicMock()
        mock_ch.emit = AsyncMock()
        mock_ch.surface_triggered = False
        mock_ch.response_text = ""
        mock_ch_cls.return_value = mock_ch

        task = _make_task()
        await loop._run_task(task)

    assert mock_publisher.publish.call_count == 2
    calls = mock_publisher.publish.call_args_list
    assert calls[0][0][0].type == RavnEventType.TASK_STARTED
    assert calls[1][0][0].type == RavnEventType.TASK_COMPLETE


@pytest.mark.asyncio
async def test_drive_loop_run_task_publishes_task_complete_on_failure(tmp_path: Path) -> None:
    """_run_task() publishes TASK_COMPLETE with success=False on agent error."""
    mock_publisher = AsyncMock(spec=EventPublisherPort)
    loop = _make_drive_loop_with_publisher(tmp_path, publisher=mock_publisher)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(side_effect=RuntimeError("agent exploded"))
    loop._agent_factory = MagicMock(return_value=mock_agent)

    with patch("ravn.drive_loop.SilentChannel") as mock_ch_cls:
        mock_ch = MagicMock()
        mock_ch.emit = AsyncMock()
        mock_ch.surface_triggered = False
        mock_ch.response_text = ""
        mock_ch_cls.return_value = mock_ch

        task = _make_task()
        await loop._run_task(task)

    assert mock_publisher.publish.call_count == 2
    complete_event = mock_publisher.publish.call_args_list[1][0][0]
    assert complete_event.type == RavnEventType.TASK_COMPLETE
    assert complete_event.payload["success"] is False


@pytest.mark.asyncio
async def test_drive_loop_noop_publisher_processes_task_without_errors(tmp_path: Path) -> None:
    """DriveLoop with NoOpEventPublisher processes a task without errors."""
    loop = _make_drive_loop_with_publisher(tmp_path, publisher=NoOpEventPublisher())

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=None)
    loop._agent_factory = MagicMock(return_value=mock_agent)

    with patch("ravn.drive_loop.SilentChannel") as mock_ch_cls:
        mock_ch = MagicMock()
        mock_ch.emit = AsyncMock()
        mock_ch.surface_triggered = False
        mock_ch.response_text = ""
        mock_ch_cls.return_value = mock_ch

        task = _make_task()
        # Must not raise
        await loop._run_task(task)


@pytest.mark.asyncio
async def test_drive_loop_re_deliver_surface_publishes_response(tmp_path: Path) -> None:
    """_re_deliver_surface() publishes a RESPONSE event via event_publisher."""
    mock_publisher = AsyncMock(spec=EventPublisherPort)
    loop = _make_drive_loop_with_publisher(tmp_path, publisher=mock_publisher)

    task = _make_task()
    await loop._re_deliver_surface(task, "[SURFACE] disk at 90%")

    mock_publisher.publish.assert_called_once()
    event = mock_publisher.publish.call_args[0][0]
    assert event.type == RavnEventType.RESPONSE
    assert event.payload["surface_escalation"] is True
    assert "disk at 90%" in event.payload["text"]
