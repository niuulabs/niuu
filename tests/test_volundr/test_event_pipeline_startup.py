"""Forge's audit subscriber and RabbitMQ sink fail startup instead of being skipped.

Both used to be wrapped in ``except Exception: logger.exception(...)``, so Forge
ran on with no audit trail, or with no RabbitMQ events, and looked healthy.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from volundr.main import _connect_rabbitmq_event_sink, _start_audit_subscriber


def _rmq_cfg() -> SimpleNamespace:
    return SimpleNamespace(url="amqp://broker/", exchange_name="events", exchange_type="topic")


async def test_audit_subscriber_start_failure_raises() -> None:
    bus = AsyncMock()
    bus.subscribe.side_effect = ConnectionError("bus down")
    with (
        patch(
            "volundr.main.AuditSubscriber.start", AsyncMock(side_effect=ConnectionError("bus down"))
        ),
        pytest.raises(ConnectionError, match="bus down"),
    ):
        await _start_audit_subscriber(bus, AsyncMock())


async def test_rabbitmq_without_aio_pika_raises_with_remedy(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "aio_pika", None)
    with pytest.raises(RuntimeError, match=r"event_pipeline\.rabbitmq\.enabled: false"):
        await _connect_rabbitmq_event_sink(_rmq_cfg())


async def test_rabbitmq_unreachable_broker_raises_with_remedy() -> None:
    pytest.importorskip("aio_pika")
    with (
        patch(
            "volundr.adapters.outbound.rabbitmq_event_sink.RabbitMQEventSink.connect",
            AsyncMock(side_effect=ConnectionRefusedError("refused")),
        ),
        pytest.raises(RuntimeError, match=r"event_pipeline\.rabbitmq\.url"),
    ):
        await _connect_rabbitmq_event_sink(_rmq_cfg())
