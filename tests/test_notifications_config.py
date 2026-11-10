"""NotificationsConfig defaults, validation and composition."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from volundr.config import NotificationDispatcherConfig, NotificationsConfig, Settings
from volundr.domain.models import Session


def test_defaults_are_safe():
    config = NotificationsConfig()
    assert config.enabled
    assert not config.reply_ready.enabled  # one per final reply was noise; opt in if wanted
    assert (config.default_page_size, config.max_page_size) == (50, 200)
    assert config.sinks == []  # no external delivery by default
    assert config.dispatcher.max_attempts >= 1
    assert Settings().notifications == config


@pytest.mark.parametrize(
    "values",
    [
        {"default_page_size": 300},
        {"reply_ready": {"title_chars": 201}},
        {"reply_ready": {"body_chars": 4001}},
        {"sinks": [{"adapter": "a.B"}]},
        {"sinks": [{"name": "ops"}]},
        {"sinks": [{"name": "ops", "adapter": "nodots"}]},
        {"sinks": [{"name": "Ops Hook", "adapter": "a.B"}]},
        {"sinks": [{"name": "integration", "adapter": "a.B"}]},
        {"sinks": [{"name": "ops", "adapter": "a.B"}, {"name": "ops", "adapter": "c.D"}]},
    ],
)
def test_invalid_configuration_fails_loudly(values):
    with pytest.raises(ValidationError):
        NotificationsConfig(**values)


def test_backoff_must_be_ordered():
    with pytest.raises(ValidationError, match="backoff"):
        NotificationDispatcherConfig(backoff_base_seconds=10, backoff_max_seconds=5)


def test_composition_builds_service_from_config():
    from volundr.main import _create_notification_service

    settings = Settings(
        notifications={
            "sinks": [
                {"name": "ops", "label": "Ops", "adapter": "a.B", "url": "https://x"},
                {"name": "page", "adapter": "a.C"},
            ]
        }
    )
    room_grants = MagicMock()
    service, image_worker = _create_notification_service(
        settings,
        MagicMock(),
        broadcaster=MagicMock(),
        integration_repository=None,
        session_lookup=AsyncMock(return_value=None),
        broker_target=AsyncMock(return_value=None),
        room_grants=room_grants,
    )
    assert service._room_grants is room_grants
    assert [(s.name, s.label) for s in service._sinks] == [("ops", "Ops"), ("page", "page")]
    assert service._image_scheduler is image_worker
    assert image_worker._settings.fetch_timeout_seconds == (
        settings.notifications.images.fetch_timeout_seconds
    )
    assert service._engine_resolver(Session(name="s", session_definition="skuldCodex")) == "codex"
    assert service._reply_title_chars == settings.notifications.reply_ready.title_chars

    disabled = Settings(notifications={"enabled": False})
    assert (
        _create_notification_service(
            disabled,
            MagicMock(),
            broadcaster=MagicMock(),
            integration_repository=None,
            session_lookup=AsyncMock(return_value=None),
            broker_target=AsyncMock(return_value=None),
            room_grants=MagicMock(),
        )
        is None
    )


def test_delivery_defaults():
    config = NotificationsConfig()
    assert config.dispatcher.enabled is True
    assert config.dispatcher.send_timeout_seconds < config.dispatcher.lease_seconds
    assert config.dispatcher.default_rate_limit.max_count == 60
    assert config.dispatcher.default_rate_limit.window_seconds == 3600
    assert config.integration_sinks == {
        "telegram": "niuu.adapters.notifications.telegram.TelegramNotificationSink",
        "webhook": "niuu.adapters.notifications.webhook.WebhookNotificationSink",
    }
    assert config.public_web_url == ""
    assert config.session_link_path.format(session_id="s", notification_id="n") == (
        "/volundr/sessions/s#notification-n"
    )
    assert (
        NotificationsConfig(dispatcher={"default_rate_limit": None}).dispatcher.default_rate_limit
        is None
    )


def test_catalog_telegram_connections_get_a_channel_adapter():
    telegram = next(d for d in Settings().integrations.definitions if d.slug == "telegram")
    assert telegram.adapter == "niuu.adapters.notifications.telegram.TelegramNotificationAdapter"


@pytest.mark.parametrize(
    "values",
    [
        {"dispatcher": {"send_timeout_seconds": 60, "lease_seconds": 60}},
        {"dispatcher": {"backoff_jitter_ratio": 1.5}},
        {"dispatcher": {"max_concurrent_sends": 0}},
        {"dispatcher": {"default_rate_limit": {"max_count": 0, "window_seconds": 60}}},
        {"sinks": [{"name": "ops", "adapter": "a.B", "secret_kwargs_env": ["secret"]}]},
        {"sinks": [{"name": "ops", "adapter": "a.B", "secret_kwargs_env": {"secret": 1}}]},
        {"integration_sinks": {"telegram": "nodots"}},
        {"integration_sinks": {"": "a.B"}},
        {"session_link_path": "/s/{session}"},
        {"feed_link_path": "/f/{session_id}"},
    ],
)
def test_invalid_delivery_configuration_fails_loudly(values):
    with pytest.raises(ValidationError):
        NotificationsConfig(**values)


PUSH_SINK = {
    "name": "push",
    "adapter": "volundr.adapters.outbound.push_notification_sink.PushNotificationSink",
}


def test_needs_input_push_delivers_through_its_configured_sink():
    settings = Settings(push={"enabled": True}, notifications={"sinks": [PUSH_SINK]})
    assert settings.push.attention_sink == "push"
    custom = Settings(
        push={"enabled": True, "attention_sink": "phone"},
        notifications={"sinks": [{**PUSH_SINK, "name": "phone"}]},
    )
    assert custom.push.attention_sink == "phone"


@pytest.mark.parametrize(
    ("push", "notifications", "match"),
    [
        ({"enabled": True}, {"sinks": []}, "notifications.sinks entry named 'push'"),
        (
            {"enabled": True, "attention_sink": "phone"},
            {"sinks": [PUSH_SINK]},
            "named 'phone'",
        ),
        ({"enabled": True}, {"enabled": False, "sinks": [PUSH_SINK]}, "notifications.enabled"),
    ],
)
def test_needs_input_push_without_its_sink_fails_loudly(push, notifications, match):
    with pytest.raises(ValidationError, match=match):
        Settings(push=push, notifications=notifications)


def test_push_off_needs_no_sink():
    assert Settings(push={"enabled": False}).push.enabled is False


class TestOdinReviewRelayComposition:
    RELAY = {
        "enabled": True,
        "owner_ids": ["jozef"],
        "source": {"kwargs": {"base_url": "https://ravn.example"}},
    }

    @pytest.mark.parametrize(
        ("gateway_on", "reason"),
        [(True, "the push gateway alerts instead"), (False, "push.enabled is false")],
    )
    async def test_unserved_push_rules_are_retired_at_start_and_said(
        self, caplog, gateway_on, reason
    ):
        from volundr.main import _retire_unserved_push_rules

        service = MagicMock()
        service.retire_attention_push_rules = AsyncMock(return_value=2)
        with caplog.at_level(logging.INFO, logger="volundr.main"):
            retired = await _retire_unserved_push_rules(Settings(), service, gateway_on=gateway_on)

        assert retired == 2
        service.retire_attention_push_rules.assert_awaited_once_with("push")
        assert any("Retired 2 needs-input push rule(s)" in r.message for r in caplog.records)
        assert any(reason in r.message for r in caplog.records)

    async def test_nothing_retired_is_not_announced(self, caplog):
        from volundr.main import _retire_unserved_push_rules

        service = MagicMock()
        service.retire_attention_push_rules = AsyncMock(return_value=0)
        with caplog.at_level(logging.INFO, logger="volundr.main"):
            assert await _retire_unserved_push_rules(Settings(), service, gateway_on=True) == 0
        assert not any("Retired" in r.message for r in caplog.records)


def test_image_defaults_and_timing():
    from volundr.config import NotificationImagesConfig

    images = NotificationsConfig().images
    assert images.thumbnail_max_side == 320
    assert images.allow_private_networks is False
    assert images.fetch_timeout_seconds < images.lease_seconds
    assert images.cache_max_age_seconds == 31_536_000
    with pytest.raises(ValidationError, match="fetch_timeout_seconds must be shorter"):
        NotificationImagesConfig(fetch_timeout_seconds=60, lease_seconds=60)
    with pytest.raises(ValidationError):
        NotificationImagesConfig(thumbnail_jpeg_quality=100)
