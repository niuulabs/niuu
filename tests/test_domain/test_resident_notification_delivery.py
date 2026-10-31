"""A resident's notification is delivered like a session's, with a link to the resident.

The dispatcher renders the deep link to the resident's chat (its main room or the
channel it came from) and every outbound shape carries ``resident_id`` /
``resident_session_id``, so a sink or an app can open the right place.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from niuu.domain.notifications import NotificationKind, NotificationSeverity, NotificationSource
from niuu.ports.notifications import OutboundNotification
from tests.test_adapters.test_push_notification_sink import Devices, RecordingChannel, ios
from tests.test_domain.test_notification_dispatcher import Harness, renderer
from volundr.adapters.outbound.push_channels import (
    ApnsNotificationChannel,
    WebhookNotificationChannel,
)
from volundr.adapters.outbound.push_notification_sink import PushNotificationSink
from volundr.domain.models import PushMessage
from volundr.domain.notifications import (
    RESIDENT_ID_KEY,
    RESIDENT_SESSION_ID_KEY,
    DeliveryStatus,
)

RESIDENT = UUID("7a7a7a7a-0000-4000-8000-000000000002")
CHANNEL = UUID("6f1c2b3a-4d5e-4f60-8a9b-0c1d2e3f4a5b")
_TEST_P8 = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg47BWBTYgy58QhfDc
2xxgtw2Vfi5RDO+VdInPdZFFLOmhRANCAAS4PbtpY+C7NQWBP/f6iAHllY4povbT
xdaWFHucGzVFS+6FcW6Q1C6VqyYnpS48MBB/4EGS5QGUh8jRKW9zw6qK
-----END PRIVATE KEY-----"""


class TestDeepLinks:
    def test_a_session_link_wins_then_the_resident_channel_then_its_room(self):
        links = renderer()
        assert links.deep_link("n", "s") == (
            "https://forge.example.com/volundr/session/s#notification-n"
        )
        assert links.deep_link("n", None, resident_id="r", resident_session_id="c") == (
            "https://forge.example.com/ravn?ravn=r&tab=chat&session=c"
        )
        assert links.deep_link("n", None, resident_id="r") == (
            "https://forge.example.com/ravn?ravn=r&tab=chat"
        )
        assert links.deep_link("n", None) == "https://forge.example.com/volundr/notifications"

    def test_no_public_origin_means_no_link(self):
        assert renderer(base="").deep_link("n", None, resident_id="r") is None


class TestDispatch:
    async def test_a_resident_channels_notification_reaches_the_sink_with_its_anchor(self):
        h = Harness()
        h.rule()
        notification = await h.notify(
            session_id=None,
            session_seq=None,
            session_name="Muninn",
            links=[],
            metadata={RESIDENT_ID_KEY: str(RESIDENT), RESIDENT_SESSION_ID_KEY: str(CHANNEL)},
        )

        assert await h.dispatcher.run_once() == 1

        assert h.only().status is DeliveryStatus.DELIVERED
        (message,) = h.sink.sent
        assert message.notification_id == str(notification.id)
        assert message.session_id is None and message.session_label == "Muninn"
        assert (message.resident_id, message.resident_session_id) == (str(RESIDENT), str(CHANNEL))
        assert message.url == (
            f"https://forge.example.com/ravn?ravn={RESIDENT}&tab=chat&session={CHANNEL}"
        )
        payload = message.to_payload()
        assert payload["resident_id"] == str(RESIDENT)
        assert payload["resident_session_id"] == str(CHANNEL)

    async def test_a_main_room_notification_links_to_the_resident(self):
        h = Harness()
        h.rule()
        await h.notify(session_id=None, metadata={RESIDENT_ID_KEY: str(RESIDENT)})
        await h.dispatcher.run_once()
        (message,) = h.sink.sent
        assert (message.resident_id, message.resident_session_id) == (str(RESIDENT), None)
        assert message.url == f"https://forge.example.com/ravn?ravn={RESIDENT}&tab=chat"

    async def test_a_sessions_message_has_no_resident_anchor(self):
        h = Harness()
        h.rule()
        await h.notify()
        await h.dispatcher.run_once()
        (message,) = h.sink.sent
        assert message.resident_id is None and message.resident_session_id is None
        assert message.to_payload()["resident_id"] is None


def _resident_outbound(**overrides) -> OutboundNotification:
    values = {
        "notification_id": "n-1",
        "title": "Muninn needs your input",
        "kind": NotificationKind.ATTENTION,
        "severity": NotificationSeverity.WARNING,
        "source": NotificationSource.SYSTEM,
        "created_at": datetime(2026, 10, 16, tzinfo=UTC),
        "body": "Which database?",
        "owner_id": "user-1",
        "url": f"https://forge.example.com/ravn?ravn={RESIDENT}&tab=chat",
        "resident_id": str(RESIDENT),
        "metadata": {"request_id": "operator:case-1:3"},
    }
    values.update(overrides)
    return OutboundNotification(**values)


def _push(**overrides) -> PushMessage:
    values = {
        "owner_id": "user-1",
        "title": "Muninn needs your input",
        "body": "Which database?",
        "session_id": "",
        "kind": "attention",
        "urgency": 0.8,
        "event_type": "session.notification",
        "notification_id": "n-1",
        "url": "https://forge.example.com/ravn",
        "resident_id": str(RESIDENT),
        "resident_session_id": str(CHANNEL),
    }
    values.update(overrides)
    return PushMessage(**values)


class TestPush:
    async def test_the_push_sink_carries_the_resident(self):
        channel = RecordingChannel()
        sink = PushNotificationSink(device_repository=Devices([ios()]), push_channel=channel)

        await sink.send(_resident_outbound())

        ((message, _),) = channel.delivered
        assert message.session_id == ""
        assert (message.resident_id, message.resident_session_id) == (str(RESIDENT), "")
        assert message.request_id == "operator:case-1:3"

    async def test_apns_payload_names_the_resident_and_channel(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        channel = ApnsNotificationChannel(
            team_id="TEAM123",
            key_id="KEY123",
            private_key=_TEST_P8,
            bundle_id="com.niuu.forge",
            client=client,
        )

        await channel.send(_push(), [ios()])

        body = client.post.call_args[1]["json"]
        assert body["resident_id"] == str(RESIDENT)
        assert body["resident_session_id"] == str(CHANNEL)
        assert body["notification_id"] == "n-1"

    async def test_relay_payload_omits_the_channel_for_the_main_room(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=202))
        channel = WebhookNotificationChannel(url="https://relay.example/push", client=client)

        await channel.send(_push(resident_session_id=""), [ios()])

        payload = client.post.call_args[1]["json"]
        assert payload["resident_id"] == str(RESIDENT)
        assert "resident_session_id" not in payload

    async def test_a_sessions_push_has_no_resident_keys(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=MagicMock(status_code=202))
        channel = WebhookNotificationChannel(url="https://relay.example/push", client=client)

        await channel.send(_push(resident_id="", resident_session_id="", session_id="s"), [ios()])

        payload = client.post.call_args[1]["json"]
        assert "resident_id" not in payload and "resident_session_id" not in payload
