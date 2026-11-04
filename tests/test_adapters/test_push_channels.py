"""Tests for the push NotificationChannel adapters."""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import httpx
import pytest

from niuu.ports.notifications import NotificationDeliveryError
from volundr.adapters.outbound.push_channels import (
    ApnsNotificationChannel,
    LoggingNotificationChannel,
    WebhookNotificationChannel,
)
from volundr.domain.models import DevicePlatform, DeviceToken, PushMessage
from volundr.domain.push import PushTokenKind

# A throwaway EC P-256 private key (test-only) for the APNs ES256 JWT path.
_TEST_P8 = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg47BWBTYgy58QhfDc
2xxgtw2Vfi5RDO+VdInPdZFFLOmhRANCAAS4PbtpY+C7NQWBP/f6iAHllY4povbT
xdaWFHucGzVFS+6FcW6Q1C6VqyYnpS48MBB/4EGS5QGUh8jRKW9zw6qK
-----END PRIVATE KEY-----"""


def _message() -> PushMessage:
    return PushMessage(
        owner_id="user-1",
        title="fix-auth needs you",
        body="Which database?",
        session_id="sess-1",
        kind="question",
        urgency=0.9,
        request_id="askq-1",
    )


def _ios_device(token: str = "dev-token") -> DeviceToken:
    return DeviceToken(owner_id="user-1", platform=DevicePlatform.IOS, token=token)


class TestLoggingChannel:
    async def test_send_does_not_raise(self):
        await LoggingNotificationChannel().send(_message(), [_ios_device()])


class TestWebhookChannel:
    async def test_posts_payload(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        channel = WebhookNotificationChannel(url="https://relay.example/push", client=client)

        await channel.send(_message(), [_ios_device("t1")])

        url, kwargs = client.post.call_args[0], client.post.call_args[1]
        assert url[0] == "https://relay.example/push"
        payload = kwargs["json"]
        assert payload["type"] == "session.needs_input"
        assert payload["session_id"] == "sess-1"
        assert payload["device_tokens"] == [{"platform": "ios", "token": "t1"}]

    async def test_signs_when_secret_set(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        channel = WebhookNotificationChannel(
            url="https://relay.example/push", secret="shh", client=client
        )

        await channel.send(_message(), [])

        headers = client.post.call_args[1]["headers"]
        assert headers["X-Niuu-Signature"].startswith("sha256=")

    async def test_no_url_raises(self):
        client = MagicMock()
        client.post = AsyncMock()
        channel = WebhookNotificationChannel(url="", client=client)

        with pytest.raises(NotificationDeliveryError, match="no url configured"):
            await channel.send(_message(), [])

        client.post.assert_not_called()

    async def test_delivery_error_raises_without_the_url(self):
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.ConnectError("down: /push/secret"))
        channel = WebhookNotificationChannel(url="https://relay.example/push/secret", client=client)

        with pytest.raises(NotificationDeliveryError, match="ConnectError") as raised:
            await channel.send(_message(), [])
        assert "secret" not in str(raised.value)

    async def test_rejected_push_raises(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=httpx.Response(502))
        channel = WebhookNotificationChannel(url="https://relay.example/push", client=client)

        with pytest.raises(NotificationDeliveryError, match="HTTP 502") as raised:
            await channel.send(_message(), [])
        assert raised.value.retryable is True


class TestApnsChannel:
    def _channel(self, client, use_sandbox=False) -> ApnsNotificationChannel:
        return ApnsNotificationChannel(
            team_id="TEAM123",
            key_id="KEY123",
            private_key=_TEST_P8,
            bundle_id="com.niuu.forge",
            use_sandbox=use_sandbox,
            client=client,
        )

    async def test_no_ios_devices_is_noop(self):
        client = MagicMock()
        client.post = AsyncMock()
        channel = self._channel(client)

        await channel.send(
            _message(), [DeviceToken(owner_id="u", platform=DevicePlatform.WEB, token="w")]
        )

        client.post.assert_not_called()

    async def test_posts_per_ios_device_with_auth(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        channel = self._channel(client)

        await channel.send(_message(), [_ios_device("d1"), _ios_device("d2")])

        assert client.post.call_count == 2
        url = client.post.call_args_list[0][0][0]
        parsed = urlparse(url)
        assert parsed.path.endswith("/3/device/d1")
        assert parsed.scheme == "https"
        assert parsed.hostname == "api.push.apple.com"
        headers = client.post.call_args_list[0][1]["headers"]
        assert headers["authorization"].startswith("bearer ")
        assert headers["apns-topic"] == "com.niuu.forge"
        body = client.post.call_args_list[0][1]["json"]
        assert body["aps"]["interruption-level"] == "time-sensitive"
        assert body["session_id"] == "sess-1"

    async def test_sandbox_host(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        channel = self._channel(client, use_sandbox=True)

        await channel.send(_message(), [_ios_device("d1")])

        parsed = urlparse(client.post.call_args[0][0])
        assert parsed.scheme == "https"
        assert parsed.hostname == "api.sandbox.push.apple.com"

    async def test_jwt_is_cached(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        channel = self._channel(client)

        first = channel._auth_token()
        second = channel._auth_token()
        assert first == second

    async def test_per_device_bundle_override(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        channel = self._channel(client)
        device = DeviceToken(
            owner_id="u",
            platform=DevicePlatform.IOS,
            token="d1",
            app_bundle_id="com.niuu.forge.widget",
        )

        await channel.send(_message(), [device])

        assert client.post.call_args[1]["headers"]["apns-topic"] == "com.niuu.forge.widget"

    async def test_only_alert_tokens_receive_the_alert(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        channel = self._channel(client)
        others = [
            DeviceToken(owner_id="u", platform=DevicePlatform.IOS, token=kind.value, kind=kind)
            for kind in PushTokenKind
            if kind is not PushTokenKind.ALERT
        ]

        await channel.send(_message(), others)
        client.post.assert_not_called()

        await channel.send(_message(), [*others, _ios_device("alert")])
        assert client.post.call_count == 1
        assert client.post.call_args[0][0].endswith("/3/device/alert")

    def test_key_from_a_file(self, tmp_path):
        path = tmp_path / "AuthKey_KEY123.p8"
        path.write_text(_TEST_P8)
        channel = ApnsNotificationChannel(
            team_id="TEAM123",
            key_id="KEY123",
            bundle_id="com.niuu.forge",
            private_key_path=str(path),
        )
        assert channel._auth_token().count(".") == 2

    @pytest.mark.parametrize(
        "keys",
        [{}, {"private_key": _TEST_P8, "private_key_path": "/etc/key.p8"}],
    )
    def test_exactly_one_key_source_is_required(self, keys):
        with pytest.raises(ValueError, match="exactly one of private_key_path and private_key"):
            ApnsNotificationChannel(
                team_id="TEAM123", key_id="KEY123", bundle_id="com.niuu.forge", **keys
            )

    def test_a_wrong_key_is_refused_when_the_channel_is_built(self, tmp_path):
        with pytest.raises(ValueError, match="not an unencrypted PEM private key"):
            ApnsNotificationChannel(
                team_id="TEAM123", key_id="KEY123", bundle_id="com.niuu.forge", private_key="nope"
            )
        with pytest.raises(ValueError, match="cannot be read"):
            ApnsNotificationChannel(
                team_id="TEAM123",
                key_id="KEY123",
                bundle_id="com.niuu.forge",
                private_key_path=str(tmp_path / "missing.p8"),
            )


class TestApnsCategory:
    """A push can name an APNs category and carry custom keys; others are untouched."""

    def _channel(self, client) -> ApnsNotificationChannel:
        return ApnsNotificationChannel(
            team_id="TEAM123",
            key_id="KEY123",
            private_key=_TEST_P8,
            bundle_id="com.niuu.forge",
            client=client,
        )

    async def test_a_question_push_carries_category_and_custom_keys(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        message = PushMessage(
            owner_id="user-1",
            title="regin asks",
            body="Proceed?",
            session_id="",
            kind="attention",
            urgency=0.8,
            notification_id="n-1",
            category="NIUU_RESIDENT_QUESTION",
            data={
                "odin_review_id": "review:court_escalation:operator:abc",
                "odin_review_decide_path": "/api/v1/ravn/odin/reviews/x/decide",
            },
        )

        await self._channel(client).deliver(message, [_ios_device("d1")])

        body = client.post.call_args[1]["json"]
        assert body == {
            "aps": {
                "alert": {"title": "regin asks", "body": "Proceed?"},
                "sound": "default",
                "interruption-level": "time-sensitive",
                "category": "NIUU_RESIDENT_QUESTION",
            },
            "session_id": "",
            "kind": "attention",
            "request_id": "",
            "notification_id": "n-1",
            "odin_review_id": "review:court_escalation:operator:abc",
            "odin_review_decide_path": "/api/v1/ravn/odin/reviews/x/decide",
        }

    async def test_other_pushes_have_no_category_and_no_extra_keys(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))

        await self._channel(client).deliver(_message(), [_ios_device("d1")])

        body = client.post.call_args[1]["json"]
        assert "category" not in body["aps"]
        assert set(body) == {"aps", "session_id", "kind", "request_id"}


class TestApnsCollapseId:
    """An outbox notification collapses on its id; a push without one has no collapse id."""

    def _channel(self, client) -> ApnsNotificationChannel:
        return ApnsNotificationChannel(
            team_id="TEAM123",
            key_id="KEY123",
            private_key=_TEST_P8,
            bundle_id="com.niuu.forge",
            client=client,
        )

    async def test_a_notification_push_collapses_on_the_notification_id(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        message = replace(_message(), notification_id="5b0c8f0e-2f7a-5d1e-9c3b-1a2b3c4d5e6f")

        await self._channel(client).deliver(message, [_ios_device("d1"), _ios_device("d2")])

        for call in client.post.call_args_list:
            assert call[1]["headers"]["apns-collapse-id"] == message.notification_id

    async def test_a_push_without_a_notification_id_has_no_collapse_id(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))

        await self._channel(client).deliver(_message(), [_ios_device("d1")])

        assert "apns-collapse-id" not in client.post.call_args[1]["headers"]


class TestApnsDeviceEnvironment:
    async def test_each_device_goes_to_its_own_apns_environment(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        channel = ApnsNotificationChannel(
            team_id="TEAM123",
            key_id="KEY123",
            private_key=_TEST_P8,
            bundle_id="com.niuu.lexi.chat",
            client=client,
        )
        devices = [
            DeviceToken(
                owner_id="u", platform=DevicePlatform.IOS, token="dev", environment="sandbox"
            ),
            DeviceToken(
                owner_id="u", platform=DevicePlatform.IOS, token="tf", environment="production"
            ),
            DeviceToken(owner_id="u", platform=DevicePlatform.IOS, token="old"),
        ]

        await channel.deliver(_message(), devices)

        hosts = [urlparse(call[0][0]).hostname for call in client.post.call_args_list]
        assert hosts == ["api.sandbox.push.apple.com", "api.push.apple.com", "api.push.apple.com"]
