"""NotificationsConfig defaults, validation and composition."""

from __future__ import annotations

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
    service, image_worker = _create_notification_service(
        settings,
        MagicMock(),
        broadcaster=MagicMock(),
        integration_repository=None,
        session_lookup=AsyncMock(return_value=None),
    )
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
        "/volundr/session/s#notification-n"
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


class TestOdinReviewRelayConfig:
    def test_it_is_off_by_default_and_needs_nothing_then(self):
        config = NotificationsConfig().odin_review_relay
        assert config.enabled is False
        assert config.owner_ids == []
        assert config.poll_interval_seconds == 30.0
        assert config.source.adapter.endswith("HttpOdinReviewSource")

    def test_a_valid_relay_parses(self):
        config = NotificationsConfig(
            odin_review_relay={
                "enabled": True,
                "owner_ids": ["jozef"],
                "poll_interval_seconds": 15,
                "source": {"kwargs": {"base_url": "https://ravn.example"}},
            }
        ).odin_review_relay
        assert (config.owner_ids, config.poll_interval_seconds) == (["jozef"], 15)
        assert config.source.kwargs == {"base_url": "https://ravn.example"}

    @pytest.mark.parametrize(
        ("values", "message"),
        [
            ({"enabled": True}, "owner_ids"),
            ({"enabled": True, "owner_ids": [" "]}, "owner_ids"),
            ({"enabled": True, "owner_ids": ["a", "a"]}, "repeat"),
            ({"enabled": True, "owner_ids": ["a"], "poll_interval_seconds": 0}, "greater than 0"),
        ],
    )
    def test_enabled_but_misconfigured_fails_with_the_remedy(self, values, message):
        with pytest.raises(ValidationError, match=message):
            NotificationsConfig(odin_review_relay=values)

    def test_the_relay_cannot_run_without_the_feed_it_records_into(self):
        with pytest.raises(ValidationError, match=r"notifications\.enabled"):
            NotificationsConfig(
                enabled=False, odin_review_relay={"enabled": True, "owner_ids": ["a"]}
            )

    def test_a_disabled_relay_is_not_validated(self):
        assert (
            NotificationsConfig(odin_review_relay={"enabled": False}).odin_review_relay.enabled
            is False
        )


class TestOdinReviewRelayComposition:
    RELAY = {
        "enabled": True,
        "owner_ids": ["jozef"],
        "source": {"kwargs": {"base_url": "https://ravn.example"}},
    }

    def test_disabled_builds_nothing(self):
        from volundr.main import _create_odin_review_relay

        assert _create_odin_review_relay(Settings(), MagicMock(), MagicMock()) is None

    async def test_enabled_builds_the_relay_from_config(self):
        from volundr.adapters.outbound.http_odin_review_source import HttpOdinReviewSource
        from volundr.main import _create_odin_review_relay

        settings = Settings(
            notifications={
                "odin_review_relay": {
                    **self.RELAY,
                    "poll_interval_seconds": 12,
                    "owner_ids": ["a", "b"],
                }
            }
        )

        relay = _create_odin_review_relay(settings, MagicMock(), MagicMock())

        assert relay._owner_ids == ["a", "b"] and relay._poll_interval == 12
        assert isinstance(relay._source, HttpOdinReviewSource)
        await relay._source.close()

    async def test_secret_kwargs_come_from_the_environment(self, monkeypatch):
        from volundr.main import _create_odin_review_relay

        monkeypatch.setenv("ODIN_RELAY_TOKEN", "from-env")
        relay_config = {
            **self.RELAY,
            "source": {
                "kwargs": {
                    "base_url": "https://ravn.example",
                    "auth_adapter": "niuu.adapters.outbound.http_auth.StaticBearerTokenAuthAdapter",
                    "auth_kwargs": {"token_env": "ODIN_RELAY_TOKEN"},
                }
            },
        }
        settings = Settings(notifications={"odin_review_relay": relay_config})

        relay = _create_odin_review_relay(settings, MagicMock(), MagicMock())

        assert relay._source._auth.headers() == {"Authorization": "Bearer from-env"}
        await relay._source.close()

    def test_enabled_without_the_notification_service_is_fatal(self):
        from volundr.main import _create_odin_review_relay

        settings = Settings(notifications={"odin_review_relay": self.RELAY})

        with pytest.raises(RuntimeError, match=r"notifications\.enabled"):
            _create_odin_review_relay(settings, MagicMock(), None)

    def test_a_missing_base_url_stops_startup_with_the_remedy(self):
        from volundr.main import _create_odin_review_relay

        settings = Settings(
            notifications={"odin_review_relay": {**self.RELAY, "source": {"kwargs": {}}}}
        )

        with pytest.raises(TypeError, match="base_url"):
            _create_odin_review_relay(settings, MagicMock(), MagicMock())

    def test_a_bad_base_url_stops_startup_with_the_remedy(self):
        from volundr.main import _create_odin_review_relay

        settings = Settings(
            notifications={
                "odin_review_relay": {**self.RELAY, "source": {"kwargs": {"base_url": "ravn"}}}
            }
        )

        with pytest.raises(ValueError, match="kwargs.base_url"):
            _create_odin_review_relay(settings, MagicMock(), MagicMock())

    def test_an_unknown_source_adapter_stops_startup(self):
        from volundr.main import _create_odin_review_relay

        settings = Settings(
            notifications={
                "odin_review_relay": {**self.RELAY, "source": {"adapter": "nowhere.Missing"}}
            }
        )

        with pytest.raises(ModuleNotFoundError):
            _create_odin_review_relay(settings, MagicMock(), MagicMock())

    async def test_push_off_is_warned_not_fatal(self, caplog):
        from volundr.main import _create_odin_review_relay

        settings = Settings(notifications={"odin_review_relay": self.RELAY})
        caplog.set_level("WARNING")

        relay = _create_odin_review_relay(settings, MagicMock(), MagicMock())

        assert relay is not None
        assert any("push.enabled is false" in r.message for r in caplog.records)
        await relay._source.close()


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
