"""Tests for the push NotificationChannel adapters."""

from unittest.mock import AsyncMock, MagicMock

from volundr.adapters.outbound.push_channels import (
    ApnsNotificationChannel,
    LoggingNotificationChannel,
    WebhookNotificationChannel,
)
from volundr.domain.models import DevicePlatform, DeviceToken, PushMessage

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

    async def test_no_url_is_noop(self):
        client = MagicMock()
        client.post = AsyncMock()
        channel = WebhookNotificationChannel(url="", client=client)

        await channel.send(_message(), [])

        client.post.assert_not_called()

    async def test_delivery_error_is_swallowed(self):
        client = MagicMock()
        client.post = AsyncMock(side_effect=RuntimeError("down"))
        channel = WebhookNotificationChannel(url="https://relay.example/push", client=client)

        # Must not raise.
        await channel.send(_message(), [])


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
        assert url.endswith("/3/device/d1")
        assert url.startswith("https://api.push.apple.com")
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

        assert client.post.call_args[0][0].startswith("https://api.sandbox.push.apple.com")

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
