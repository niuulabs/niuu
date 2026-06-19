"""Tests for PushAttentionNotifier."""

from __future__ import annotations

from volundr.domain.models import (
    DevicePlatform,
    DeviceToken,
    GitSource,
    PushMessage,
    Session,
    SessionStatus,
)
from volundr.domain.ports import DeviceTokenRepository, NotificationChannel
from volundr.domain.services.attention_notifier import PushAttentionNotifier


class FakeDeviceRepo(DeviceTokenRepository):
    def __init__(self, devices: list[DeviceToken] | None = None):
        self._devices = devices or []

    async def upsert(self, device: DeviceToken) -> DeviceToken:
        self._devices.append(device)
        return device

    async def list_for_owner(self, owner_id: str) -> list[DeviceToken]:
        return [d for d in self._devices if d.owner_id == owner_id]

    async def delete(self, owner_id: str, token: str) -> bool:
        return False


class RecordingChannel(NotificationChannel):
    def __init__(self, raise_error: bool = False):
        self.sent: list[tuple[PushMessage, list[DeviceToken]]] = []
        self._raise = raise_error

    async def send(self, message: PushMessage, devices: list[DeviceToken]) -> None:
        if self._raise:
            raise RuntimeError("channel down")
        self.sent.append((message, devices))


def _session(owner_id: str | None = "user-1", name: str = "fix-auth") -> Session:
    return Session(
        name=name,
        model="claude-sonnet-4-20250514",
        source=GitSource(repo="https://github.com/test/repo", branch="main"),
        status=SessionStatus.RUNNING,
        owner_id=owner_id,
    )


async def test_dispatches_to_owner_devices():
    device = DeviceToken(owner_id="user-1", platform=DevicePlatform.IOS, token="t1")
    channel = RecordingChannel()
    notifier = PushAttentionNotifier(FakeDeviceRepo([device]), channel)

    await notifier.notify_needs_input(
        _session(), kind="question", prompt="Which DB?", request_id="askq-1"
    )

    assert len(channel.sent) == 1
    message, devices = channel.sent[0]
    assert message.owner_id == "user-1"
    assert message.body == "Which DB?"
    assert message.kind == "question"
    assert message.request_id == "askq-1"
    assert "fix-auth" in message.title
    assert devices == [device]


async def test_uses_default_body_when_no_prompt():
    channel = RecordingChannel()
    notifier = PushAttentionNotifier(FakeDeviceRepo(), channel)

    await notifier.notify_needs_input(_session(), kind="permission", prompt="", request_id="")

    assert channel.sent[0][0].body == "The agent needs permission to continue"


async def test_skips_ownerless_session():
    channel = RecordingChannel()
    notifier = PushAttentionNotifier(FakeDeviceRepo(), channel)

    await notifier.notify_needs_input(
        _session(owner_id=None), kind="question", prompt="x", request_id="r"
    )

    assert channel.sent == []


async def test_urgency_gate_blocks_low_channels():
    channel = RecordingChannel()
    # needs-input is 0.9; a channel requiring 0.95 should not receive it.
    notifier = PushAttentionNotifier(FakeDeviceRepo(), channel, min_urgency=0.95)

    await notifier.notify_needs_input(_session(), kind="question", prompt="x", request_id="r")

    assert channel.sent == []


async def test_channel_error_is_swallowed():
    notifier = PushAttentionNotifier(FakeDeviceRepo(), RecordingChannel(raise_error=True))

    # Must not raise — a push failure cannot break the activity path.
    await notifier.notify_needs_input(_session(), kind="question", prompt="x", request_id="r")
