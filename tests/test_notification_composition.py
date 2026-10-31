"""Notification delivery composition: sinks, resolver, renderer and dispatcher from config."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.adapters.notifications.integrations import IntegrationSinkResolver
from niuu.adapters.notifications.webhook import WebhookNotificationSink
from volundr.adapters.outbound.notification_sinks import NotificationSinkBuildError
from volundr.adapters.outbound.push_channels import LoggingNotificationChannel
from volundr.adapters.outbound.push_notification_sink import PushNotificationSink
from volundr.config import Settings
from volundr.domain.services.notification_dispatcher import NotificationDispatcher
from volundr.notification_composition import (
    NotificationDelivery,
    create_notification_delivery,
    create_push_channel,
    dispatcher_settings,
    notification_renderer,
)

PUSH = "volundr.adapters.outbound.push_notification_sink.PushNotificationSink"
WEBHOOK = "niuu.adapters.notifications.webhook.WebhookNotificationSink"


def build(settings: Settings, **overrides):
    values = {
        "public_origin": "http://localhost:8080",
        "integration_repository": MagicMock(),
        "credential_store": MagicMock(),
        "device_repository": MagicMock(),
        "push_channel": LoggingNotificationChannel(),
    }
    values.update(overrides)
    return create_notification_delivery(settings, MagicMock(), **values)


@pytest.mark.parametrize("notifications", [{"enabled": False}, {"dispatcher": {"enabled": False}}])
def test_delivery_is_off_when_disabled(notifications):
    assert build(Settings(notifications=notifications)) is None


async def test_builds_sinks_resolver_and_dispatcher_from_config():
    settings = Settings(
        notifications={
            "public_web_url": "https://forge.example.com/",
            "sinks": [
                {"name": "ops", "adapter": WEBHOOK, "url": "https://hooks.example.com/x"},
                {"name": "phone", "label": "Phone", "adapter": PUSH},
            ],
            "dispatcher": {"batch_size": 7, "max_attempts": 4},
        }
    )
    delivery = build(settings)

    assert isinstance(delivery, NotificationDelivery)
    assert isinstance(delivery.dispatcher, NotificationDispatcher)
    assert delivery.sinks.names == ["ops", "phone"]
    ops = delivery.sinks._sinks["ops"]  # noqa: SLF001
    phone = delivery.sinks._sinks["phone"]  # noqa: SLF001
    assert isinstance(ops, WebhookNotificationSink)
    assert isinstance(phone, PushNotificationSink)
    assert isinstance(delivery.sinks._resolver, IntegrationSinkResolver)  # noqa: SLF001
    assert delivery.dispatcher._settings.batch_size == 7  # noqa: SLF001
    assert delivery.dispatcher._renderer._base == "https://forge.example.com"  # noqa: SLF001
    assert delivery.dispatcher._renderer._host_label == "forge.example.com"  # noqa: SLF001
    await delivery.sinks.close()


def test_integration_delivery_needs_the_repository_and_credential_store():
    delivery = build(Settings(), credential_store=None)
    assert delivery.sinks._resolver is None  # noqa: SLF001


def test_push_sink_without_its_dependencies_fails_startup():
    settings = Settings(notifications={"sinks": [{"name": "phone", "adapter": PUSH}]})
    with pytest.raises(NotificationSinkBuildError, match="'phone'.*push_channel"):
        build(settings, device_repository=None, push_channel=None)


def test_renderer_falls_back_to_the_public_origin():
    config = Settings(notifications={"host_label": "thor"}).notifications
    renderer = notification_renderer(config, "http://10.0.0.5:8080")
    assert renderer.deep_link("n", "s") == "http://10.0.0.5:8080/volundr/session/s#notification-n"
    assert renderer._host_label == "thor"  # noqa: SLF001


def test_dispatcher_settings_mirror_config():
    config = Settings().notifications
    settings = dispatcher_settings(config)
    assert settings.lease_seconds == config.dispatcher.lease_seconds
    assert settings.default_rate_limit == config.dispatcher.default_rate_limit


async def test_start_and_stop_drive_the_dispatcher_and_close_sinks():
    dispatcher = MagicMock()
    dispatcher.start = AsyncMock()
    dispatcher.stop = AsyncMock(side_effect=RuntimeError("boom"))
    sinks = MagicMock()
    sinks.close = AsyncMock()
    delivery = NotificationDelivery(dispatcher=dispatcher, sinks=sinks)
    await delivery.start()
    with pytest.raises(RuntimeError):
        await delivery.stop()
    dispatcher.start.assert_awaited_once()
    sinks.close.assert_awaited_once()


def test_push_channel_comes_from_push_config():
    assert isinstance(create_push_channel(Settings()), LoggingNotificationChannel)
    bad = Settings(push={"adapter": "volundr.config.Settings"})
    with pytest.raises(TypeError, match="not a NotificationChannel"):
        create_push_channel(bad)


def test_main_builds_a_push_channel_for_push_sinks():
    from volundr.main import _create_notification_delivery

    settings = Settings(notifications={"sinks": [{"name": "phone", "adapter": PUSH}]})
    delivery = _create_notification_delivery(
        settings,
        MagicMock(),
        public_origin="http://localhost:8080",
        integration_repository=None,
        credential_store=None,
        device_repository=MagicMock(),
    )
    assert delivery.sinks.names == ["phone"]

    broken = Settings(
        push={"adapter": "no.such.Channel"},
        notifications={"sinks": [{"name": "phone", "adapter": PUSH}]},
    )
    with pytest.raises(ImportError):
        _create_notification_delivery(
            broken,
            MagicMock(),
            public_origin="http://localhost:8080",
            integration_repository=None,
            credential_store=None,
            device_repository=MagicMock(),
        )
