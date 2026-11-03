"""Push delivery for notifications: the push sink and the strict channel ``deliver`` path."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from niuu.domain.notifications import (
    NotificationKind,
    NotificationSeverity,
    NotificationSource,
)
from niuu.ports.notifications import NotificationDeliveryError, OutboundNotification
from tests.support.push_fakes import Clock, FakeDeviceRepository
from volundr.adapters.outbound.push_channels import (
    ApnsNotificationChannel,
    LoggingNotificationChannel,
    WebhookNotificationChannel,
)
from volundr.adapters.outbound.push_notification_sink import PushNotificationSink
from volundr.domain.models import DevicePlatform, DeviceToken, PushMessage
from volundr.domain.ports import NotificationChannel
from volundr.domain.push import PushTokenKind

_TEST_P8 = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg47BWBTYgy58QhfDc
2xxgtw2Vfi5RDO+VdInPdZFFLOmhRANCAAS4PbtpY+C7NQWBP/f6iAHllY4povbT
xdaWFHucGzVFS+6FcW6Q1C6VqyYnpS48MBB/4EGS5QGUh8jRKW9zw6qK
-----END PRIVATE KEY-----"""


def outbound(**overrides) -> OutboundNotification:
    values = {
        "notification_id": "n-1",
        "title": "Tests are green",
        "kind": NotificationKind.MILESTONE,
        "severity": NotificationSeverity.SUCCESS,
        "source": NotificationSource.AGENT,
        "created_at": datetime(2026, 9, 23, tzinfo=UTC),
        "body": "All   342 tests\npassed.",
        "owner_id": "user-1",
        "session_id": "sess-1",
        "url": "https://forge.example.com/volundr/session/sess-1",
        "metadata": {"request_id": "req-9"},
    }
    values.update(overrides)
    return OutboundNotification(**values)


def ios(token: str = "d1") -> DeviceToken:
    return DeviceToken(owner_id="user-1", platform=DevicePlatform.IOS, token=token)


class Devices(FakeDeviceRepository):
    def __init__(self, devices: list[DeviceToken]) -> None:
        super().__init__(Clock())
        self.add(*devices)
        self.asked: list[str] = []

    async def list_for_owner(
        self, owner_id: str, *, kinds: Sequence[PushTokenKind] | None = None
    ) -> list[DeviceToken]:
        self.asked.append(owner_id)
        return await super().list_for_owner(owner_id, kinds=kinds)


class RecordingChannel(NotificationChannel):
    def __init__(self) -> None:
        self.delivered: list[tuple[PushMessage, list[DeviceToken]]] = []

    async def send(self, message: PushMessage, devices: list[DeviceToken]) -> None:
        self.delivered.append((message, devices))


class TestPushSink:
    async def test_pushes_to_the_owners_devices(self):
        device = ios()
        devices = Devices([device])
        channel = RecordingChannel()
        sink = PushNotificationSink(device_repository=devices, push_channel=channel)

        result = await sink.send(outbound())

        assert devices.asked == ["user-1"]
        ((message, sent_to),) = channel.delivered
        assert sent_to == [device]
        assert message.event_type == "session.notification"
        assert message.title == "Tests are green"
        assert message.body == "All 342 tests passed."
        assert message.kind == "milestone"
        assert message.urgency == 0.5
        assert message.request_id == "req-9"
        assert message.notification_id == "n-1"
        assert message.url == "https://forge.example.com/volundr/session/sess-1"
        assert result.detail == "handed to RecordingChannel"
        assert sink.name == "push"

    async def test_body_is_shortened_and_urgency_is_configurable(self):
        channel = RecordingChannel()
        sink = PushNotificationSink(
            device_repository=Devices([]),
            push_channel=channel,
            max_body_chars=10,
            severity_urgency={"critical": 0.95},
            name="phone",
        )
        await sink.send(outbound(body="x" * 50, severity=NotificationSeverity.CRITICAL))
        message = channel.delivered[0][0]
        assert message.body == "x" * 9 + "…"
        assert message.urgency == 0.95
        assert sink.name == "phone"

    def test_invalid_body_limit_is_rejected(self):
        with pytest.raises(ValueError):
            PushNotificationSink(
                device_repository=Devices([]), push_channel=RecordingChannel(), max_body_chars=0
            )

    async def test_ownerless_notification_is_permanent(self):
        sink = PushNotificationSink(device_repository=Devices([]), push_channel=RecordingChannel())
        with pytest.raises(NotificationDeliveryError) as info:
            await sink.send(outbound(owner_id=""))
        assert not info.value.retryable

    async def test_system_attention_is_pushed(self):
        """The needs-input push is the outbox delivering Forge's own attention item."""
        channel = RecordingChannel()
        sink = PushNotificationSink(device_repository=Devices([ios()]), push_channel=channel)
        system_attention = outbound(
            kind=NotificationKind.ATTENTION, source=NotificationSource.SYSTEM
        )
        assert sink.skip_reason(system_attention) is None
        await sink.send(system_attention)
        [(push, devices)] = channel.delivered
        assert push.kind == "attention" and len(devices) == 1

    async def test_a_producer_can_ask_for_an_apns_category_and_custom_keys(self):
        channel = RecordingChannel()
        sink = PushNotificationSink(device_repository=Devices([ios()]), push_channel=channel)

        await sink.send(
            outbound(
                kind=NotificationKind.ATTENTION,
                source=NotificationSource.SYSTEM,
                session_id=None,
                metadata={
                    "push_category": "NIUU_RESIDENT_QUESTION",
                    "push_data": {"odin_review_id": "r1", "odin_review_decide_path": "/p"},
                },
            )
        )

        [(push, _devices)] = channel.delivered
        assert push.category == "NIUU_RESIDENT_QUESTION"
        assert push.data == {"odin_review_id": "r1", "odin_review_decide_path": "/p"}

    async def test_a_push_without_shaping_metadata_is_unchanged(self):
        channel = RecordingChannel()
        sink = PushNotificationSink(device_repository=Devices([ios()]), push_channel=channel)

        await sink.send(outbound())

        [(push, _devices)] = channel.delivered
        assert push.category == "" and dict(push.data) == {}

    @pytest.mark.parametrize(
        "metadata",
        [
            {"push_category": 7},
            {"push_data": ["odin_review_id"]},
            {"push_data": {"odin_review_id": 1}},
            {"push_data": {"aps": "x"}},
        ],
    )
    async def test_malformed_shaping_fails_the_delivery_for_good(self, metadata):
        channel = RecordingChannel()
        sink = PushNotificationSink(device_repository=Devices([ios()]), push_channel=channel)

        with pytest.raises(NotificationDeliveryError) as info:
            await sink.send(outbound(metadata=metadata))

        assert not info.value.retryable
        assert channel.delivered == []

    async def test_channel_failures_propagate(self):
        channel = MagicMock(spec=NotificationChannel)
        channel.deliver = AsyncMock(
            side_effect=NotificationDeliveryError("relay down", retryable=True)
        )
        sink = PushNotificationSink(device_repository=Devices([ios()]), push_channel=channel)
        with pytest.raises(NotificationDeliveryError, match="relay down"):
            await sink.send(outbound())


def _message(**overrides) -> PushMessage:
    values = {
        "owner_id": "user-1",
        "title": "t",
        "body": "b",
        "session_id": "s",
        "kind": "milestone",
        "urgency": 0.5,
        "event_type": "session.notification",
        "notification_id": "n-1",
        "url": "https://forge.example.com/x",
    }
    values.update(overrides)
    return PushMessage(**values)


def _client(*responses) -> MagicMock:
    client = MagicMock()
    client.post = AsyncMock(side_effect=list(responses))
    return client


class TestRelayDeliver:
    async def test_success_includes_notification_fields(self):
        client = _client(MagicMock(status_code=202))
        channel = WebhookNotificationChannel(url="https://relay.example/push/SECRET", client=client)
        detail = await channel.deliver(_message(), [ios()])
        payload = client.post.call_args[1]["json"]
        assert payload["type"] == "session.notification"
        assert payload["notification_id"] == "n-1"
        assert payload["url"] == "https://forge.example.com/x"
        assert detail == "push relay https://relay.example accepted HTTP 202"

    async def test_server_error_is_retryable(self):
        client = _client(httpx.Response(503))
        channel = WebhookNotificationChannel(url="https://relay.example/push", client=client)
        with pytest.raises(NotificationDeliveryError) as info:
            await channel.deliver(_message(), [])
        assert info.value.retryable

    async def test_network_error_is_retryable(self):
        client = _client(httpx.ConnectError("refused"))
        channel = WebhookNotificationChannel(url="https://relay.example/push", client=client)
        with pytest.raises(NotificationDeliveryError) as info:
            await channel.deliver(_message(), [])
        assert info.value.retryable

    async def test_missing_url_is_permanent_for_send_and_deliver(self):
        channel = WebhookNotificationChannel(url="", client=_client())
        with pytest.raises(NotificationDeliveryError):
            await channel.deliver(_message(), [])
        with pytest.raises(NotificationDeliveryError):
            await channel.send(_message(), [])

    async def test_send_raises_a_rejected_push(self):
        client = _client(httpx.Response(400))
        channel = WebhookNotificationChannel(url="https://relay.example/push", client=client)
        with pytest.raises(NotificationDeliveryError, match="HTTP 400") as info:
            await channel.send(_message(), [])
        assert not info.value.retryable

    async def test_default_deliver_calls_send(self):
        detail = await LoggingNotificationChannel().deliver(_message(), [ios()])
        assert detail == "handed to LoggingNotificationChannel"

    async def test_the_relay_gets_the_same_category_and_keys(self):
        client = _client(MagicMock(status_code=202))
        channel = WebhookNotificationChannel(url="https://relay.example/push", client=client)

        await channel.deliver(
            _message(category="NIUU_RESIDENT_QUESTION", data={"odin_review_id": "r1"}), []
        )

        payload = client.post.call_args[1]["json"]
        assert payload["category"] == "NIUU_RESIDENT_QUESTION"
        assert payload["odin_review_id"] == "r1"

    async def test_the_relay_payload_is_unchanged_without_a_category(self):
        client = _client(MagicMock(status_code=202))
        channel = WebhookNotificationChannel(url="https://relay.example/push", client=client)

        await channel.deliver(_message(), [])

        assert "category" not in client.post.call_args[1]["json"]

    @pytest.mark.parametrize("key", ["aps", "session_id", "kind", "notification_id", "url"])
    def test_custom_keys_cannot_overwrite_the_payload(self, key):
        with pytest.raises(ValueError, match=key):
            _message(data={key: "x"})


class TestApnsDeliver:
    def _channel(self, client) -> ApnsNotificationChannel:
        return ApnsNotificationChannel(
            team_id="TEAM", key_id="KEY", private_key=_TEST_P8, bundle_id="b", client=client
        )

    async def test_partial_success_counts_as_delivered(self, caplog):
        client = _client(MagicMock(status_code=200), MagicMock(status_code=410))
        with caplog.at_level("WARNING"):
            detail = await self._channel(client).deliver(_message(), [ios("a"), ios("b")])
        assert detail == "APNs accepted 1 of 2 devices"
        assert "APNs rejected push (status=410)" in caplog.text
        body = client.post.call_args_list[0][1]["json"]
        assert body["notification_id"] == "n-1" and body["url"] == "https://forge.example.com/x"

    async def test_all_devices_failing_transiently_is_retryable(self):
        client = _client(MagicMock(status_code=503), RuntimeError("reset"))
        with pytest.raises(NotificationDeliveryError) as info:
            await self._channel(client).deliver(_message(), [ios("a"), ios("b")])
        assert info.value.retryable
        assert "none of 2 devices" in str(info.value)

    async def test_all_devices_rejected_is_permanent(self):
        client = _client(MagicMock(status_code=400))
        with pytest.raises(NotificationDeliveryError) as info:
            await self._channel(client).deliver(_message(), [ios()])
        assert not info.value.retryable

    async def test_no_ios_devices_is_permanent(self):
        web = DeviceToken(owner_id="u", platform=DevicePlatform.WEB, token="w")
        with pytest.raises(NotificationDeliveryError, match="no registered iOS devices"):
            await self._channel(_client()).deliver(_message(), [web])

    def test_bad_key_is_refused_when_the_channel_is_built(self):
        with pytest.raises(ValueError, match="not an unencrypted PEM private key"):
            ApnsNotificationChannel(
                team_id="T", key_id="K", private_key="not a key", bundle_id="b", client=_client()
            )

    async def test_a_client_that_cannot_be_built_is_permanent(self, monkeypatch):
        channel = self._channel(_client())

        async def no_http2() -> None:
            raise ImportError("h2 is not installed")

        monkeypatch.setattr(channel, "_get_client", no_http2)
        with pytest.raises(NotificationDeliveryError, match="setup failed: ImportError") as info:
            await channel.deliver(_message(), [ios()])
        assert not info.value.retryable

    async def test_send_raises_what_deliver_raises(self):
        client = _client(MagicMock(status_code=500))
        with pytest.raises(NotificationDeliveryError, match="none of 1 devices") as info:
            await self._channel(client).send(_message(), [ios()])
        assert info.value.retryable

    async def test_send_without_ios_devices_has_nothing_to_deliver(self):
        client = _client()
        web = DeviceToken(owner_id="u", platform=DevicePlatform.WEB, token="w")
        await self._channel(client).send(_message(), [web])
        client.post.assert_not_called()


class TestAlertTokensOnly:
    async def test_live_activity_and_widget_tokens_never_get_the_alert(self):
        channel = RecordingChannel()
        alert = ios("alert-1")
        others = [
            DeviceToken(owner_id="user-1", platform=DevicePlatform.IOS, token=kind.value, kind=kind)
            for kind in PushTokenKind
            if kind is not PushTokenKind.ALERT
        ]
        sink = PushNotificationSink(
            device_repository=Devices([alert, *others]), push_channel=channel
        )

        await sink.send(outbound())

        ((_, devices),) = channel.delivered
        assert [device.token for device in devices] == ["alert-1"]
