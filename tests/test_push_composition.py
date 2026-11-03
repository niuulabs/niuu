"""``push.apns`` / ``push.gateway`` configuration and the gateway built from it."""

from __future__ import annotations

import builtins

import pytest
from pydantic import ValidationError

from niuu.domain.notifications import NotificationKind, NotificationSeverity
from tests.helpers.apns_keys import generate_rsa_pem, write_p8
from tests.support.push_fakes import Clock, FakeDeviceRepository
from volundr.adapters.outbound.apns_client import ApnsClient, ApnsConfigurationError
from volundr.adapters.outbound.http_guild_feed import HttpGuildFeed
from volundr.adapters.outbound.postgres_push_gateway import PostgresPushDeliveryRepository
from volundr.config import (
    ApnsConfig,
    PushAlertsConfig,
    PushGatewayConfig,
    PushNotificationConfig,
    Settings,
)
from volundr.domain.services.push_gateway import PushGateway
from volundr.push_composition import (
    create_apns_client,
    create_push_gateway,
    push_gateway_settings,
    read_reader_token,
)

LOGGING = "volundr.adapters.outbound.push_channels.LoggingNotificationChannel"


def push_config(key_path, **gateway) -> dict:
    return {
        "enabled": True,
        "apns": {
            "team_id": "828357L924",
            "key_id": "SSU64X5VGZ",
            "bundle_id": "com.xteo.lexi.chat",
            "private_key_path": str(key_path),
        },
        "gateway": {"enabled": True, "guild_url": "http://127.0.0.1:8080", **gateway},
    }


class TestDefaults:
    def test_push_is_off_and_the_gateway_is_off(self):
        push = Settings().push
        assert push.enabled is False and push.gateway.enabled is False
        assert push.adapter == LOGGING
        assert create_push_gateway(push, object(), FakeDeviceRepository(Clock())) is None

    def test_gateway_defaults(self):
        gateway = PushGatewayConfig()
        assert gateway.poll_interval_seconds == 5.0 and gateway.stream_enabled is False
        assert gateway.request_timeout_seconds == 5.0 and gateway.node_retry_seconds == 30.0
        assert gateway.alerts.kinds == [
            NotificationKind.MILESTONE,
            NotificationKind.DECISION,
            NotificationKind.ATTENTION,
            NotificationKind.ERROR,
        ]
        assert gateway.alerts.attention_kinds == [
            NotificationKind.ATTENTION,
            NotificationKind.DECISION,
            NotificationKind.ERROR,
        ]
        assert gateway.alerts.min_severity is NotificationSeverity.INFO
        assert gateway.alerts.rate_limit.max_count == 30
        assert gateway.alerts.rate_limit.window_seconds == 3600
        assert gateway.alerts.quiet_hours is None
        assert gateway.alerts.category == "FORGE_ACTIVITY"
        assert gateway.alerts.deep_link_base == "lexichat://activity"
        assert gateway.live_activity.stale_seconds == 28800
        assert gateway.live_activity.priority == 10
        assert gateway.live_activity.start.enabled is False
        assert gateway.live_activity.start.min_interval_seconds == 3600.0
        assert gateway.widgets.min_interval_seconds == 120.0
        assert gateway.widgets.priority == 10
        assert gateway.background.enabled is True
        assert gateway.background.min_interval_seconds == 900.0
        assert gateway.background.priority == 5

    def test_apns_hosts(self):
        apns = ApnsConfig()
        assert apns.production_url == "https://api.push.apple.com"
        assert apns.sandbox_url == "https://api.sandbox.push.apple.com"
        assert apns.token_ttl_seconds == 3000


class TestValidation:
    def test_gateway_needs_the_guild_url(self):
        with pytest.raises(ValidationError, match="needs push.gateway.guild_url"):
            PushGatewayConfig(enabled=True)

    def test_gateway_needs_push_enabled(self, tmp_path):
        config = push_config(write_p8(tmp_path))
        config["enabled"] = False
        with pytest.raises(ValidationError, match="needs push.enabled: true"):
            PushNotificationConfig(**config)

    @pytest.mark.parametrize("field", ["team_id", "key_id", "bundle_id", "private_key_path"])
    def test_gateway_needs_every_apns_setting(self, tmp_path, field):
        config = push_config(write_p8(tmp_path))
        config["apns"][field] = " "
        with pytest.raises(ValidationError, match=f"push.apns.{field}"):
            PushNotificationConfig(**config)

    @pytest.mark.parametrize(
        "extra",
        [
            {"adapter": "volundr.adapters.outbound.push_channels.ApnsNotificationChannel"},
            {"kwargs": {"team_id": "X"}},
            {"secret_kwargs_env": {"private_key": "APNS_PRIVATE_KEY"}},
        ],
    )
    def test_the_gateway_replaces_the_per_node_channel(self, tmp_path, extra):
        with pytest.raises(ValidationError, match="which the gateway replaces"):
            PushNotificationConfig(**push_config(write_p8(tmp_path)), **extra)

    def test_backoff_order(self):
        with pytest.raises(ValidationError, match="backoff_base_seconds must not exceed"):
            PushAlertsConfig(backoff_base_seconds=700)

    @pytest.mark.parametrize("ttl", [60, 3600])
    def test_token_ttl_stays_inside_apples_window(self, ttl):
        with pytest.raises(ValidationError):
            ApnsConfig(token_ttl_seconds=ttl)

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValidationError):
            PushAlertsConfig(kinds=["milestone", "applause"])

    def test_settings_load_the_operator_block(self, tmp_path):
        settings = Settings(
            push=push_config(
                write_p8(tmp_path),
                public_api_url="http://thor.tail737f2a.ts.net:8080",
                origin_label="Thor",
                alerts={
                    "quiet_hours": {"start": "23:00", "end": "07:00", "timezone": "Europe/Paris"},
                    "rate_limit": None,
                },
                live_activity={"start": {"enabled": True}},
            )
        )
        gateway = settings.push.gateway
        assert gateway.alerts.quiet_hours.timezone == "Europe/Paris"
        assert gateway.alerts.rate_limit is None
        assert gateway.live_activity.start.enabled is True


class TestComposition:
    async def test_builds_the_gateway_from_config(self, tmp_path):
        push = PushNotificationConfig(
            **push_config(
                write_p8(tmp_path),
                public_api_url=" http://thor.tail737f2a.ts.net:8080 ",
                origin_label=" Thor ",
            )
        )
        gateway = create_push_gateway(push, object(), FakeDeviceRepository(Clock()))

        assert isinstance(gateway, PushGateway)
        assert isinstance(gateway._feed, HttpGuildFeed)
        assert gateway._feed._feed_url == "http://127.0.0.1:8080/api/v1/forge/notifications"
        assert gateway._feed._headers == {}
        assert isinstance(gateway._apns, ApnsClient)
        assert isinstance(gateway._deliveries, PostgresPushDeliveryRepository)
        settings = gateway._settings
        assert settings.bundle_id == "com.xteo.lexi.chat"
        assert settings.public_api_url == "http://thor.tail737f2a.ts.net:8080"
        assert settings.origin_label == "Thor"
        await gateway.stop()

    def test_settings_mapping(self, tmp_path):
        push = PushNotificationConfig(
            **push_config(
                write_p8(tmp_path),
                alerts={
                    "kinds": ["error"],
                    "sources": ["agent"],
                    "low_priority_kinds": ["info"],
                    "attention_kinds": ["error"],
                },
                live_activity={
                    "priority": 5,
                    "start": {"enabled": True, "min_interval_seconds": 60},
                },
                widgets={"enabled": False, "min_interval_seconds": 600, "priority": 10},
                background={"enabled": False, "min_interval_seconds": 1800},
            )
        )

        settings = push_gateway_settings(push)

        assert settings.alerts.kinds == frozenset({"error"})
        assert settings.alerts.sources == frozenset({"agent"})
        assert settings.alerts.low_priority_kinds == frozenset({"info"})
        assert settings.alerts.attention_kinds == frozenset({"error"})
        assert settings.alerts.rate_limit.max_count == 30
        assert settings.live_activity.priority == 5
        assert settings.live_activity.start_enabled is True
        assert settings.live_activity.start_min_interval_seconds == 60
        assert (settings.widgets.enabled, settings.widgets.min_interval_seconds) == (False, 600)
        assert settings.widgets.priority == 10
        assert (settings.background.enabled, settings.background.min_interval_seconds) == (
            False,
            1800,
        )
        assert settings.background.priority == 5
        assert settings.poll_interval_seconds == 5.0 and settings.page_size == 50
        assert settings.stream_enabled is False
        assert settings.node_retry_seconds == 30.0

    def test_a_missing_key_file_stops_the_start(self, tmp_path):
        push = PushNotificationConfig(**push_config(tmp_path / "AuthKey_MISSING.p8"))
        with pytest.raises(ApnsConfigurationError, match="cannot be read"):
            create_push_gateway(push, object(), FakeDeviceRepository(Clock()))

    def test_a_wrong_key_stops_the_start(self, tmp_path):
        path = tmp_path / "key.p8"
        path.write_text(generate_rsa_pem())
        push = PushNotificationConfig(**push_config(path))
        with pytest.raises(ApnsConfigurationError, match="not an EC P-256 key"):
            create_push_gateway(push, object(), FakeDeviceRepository(Clock()))

    def test_missing_http2_support_stops_the_start(self, tmp_path, monkeypatch):
        real_import = builtins.__import__

        def no_h2(name, *args, **kwargs):
            if name == "h2" or name.startswith("h2."):
                raise ImportError("No module named 'h2'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_h2)
        config = ApnsConfig(**push_config(write_p8(tmp_path))["apns"])

        with pytest.raises(ApnsConfigurationError, match="the 'h2' package is not installed"):
            create_apns_client(config)

    async def test_the_real_client_speaks_http2(self, tmp_path):
        client = create_apns_client(ApnsConfig(**push_config(write_p8(tmp_path))["apns"]))
        assert isinstance(client, ApnsClient)
        await client.close()

    def test_a_bad_guild_url_stops_the_start(self, tmp_path):
        push = PushNotificationConfig(**push_config(write_p8(tmp_path), guild_url="guild:8080"))
        with pytest.raises(ValueError, match="http or https"):
            create_push_gateway(push, object(), FakeDeviceRepository(Clock()))


class TestReaderToken:
    def test_no_path_means_no_credential(self):
        assert read_reader_token("") == ""

    def test_reads_and_strips_the_token(self, tmp_path):
        path = tmp_path / "reader-token"
        path.write_text("pat-abc123\n")
        assert read_reader_token(str(path)) == "pat-abc123"

    def test_a_missing_file_names_the_setting(self, tmp_path):
        with pytest.raises(ValueError, match="push.gateway.reader_token_path .* cannot be read"):
            read_reader_token(str(tmp_path / "nope"))

    def test_an_empty_file_is_refused(self, tmp_path):
        path = tmp_path / "reader-token"
        path.write_text("  \n")
        with pytest.raises(ValueError, match="is empty"):
            read_reader_token(str(path))

    async def test_the_gateway_reads_the_guild_with_it(self, tmp_path):
        token = tmp_path / "reader-token"
        token.write_text("pat-abc123")
        push = PushNotificationConfig(
            **push_config(write_p8(tmp_path), reader_token_path=str(token))
        )
        gateway = create_push_gateway(push, object(), FakeDeviceRepository(Clock()))
        assert gateway._feed._headers == {"authorization": "Bearer pat-abc123"}
        await gateway.stop()
