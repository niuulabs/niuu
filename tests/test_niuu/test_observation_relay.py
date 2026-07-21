"""Tests for neutral external-event observation delivery."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from niuu.collaboration import Participant
from niuu.collaboration.observation_relay import ObservationRelay
from sleipnir.domain.events import SleipnirEvent

_PEER = "resident-1"


class _Subscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class _Subscriber:
    def __init__(self) -> None:
        self.patterns: list[str] = []
        self.handler = None
        self.subscription = _Subscription()

    async def subscribe(self, patterns, handler):
        self.patterns = list(patterns)
        self.handler = handler
        return self.subscription


def _event(event_type: str, payload: dict | None = None) -> SleipnirEvent:
    return SleipnirEvent(
        event_type=event_type,
        source="ting",
        payload=payload or {},
        summary="work completed",
        urgency=0.5,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id="corr-1",
    )


def _participant(subscriptions=("research.*",)) -> Participant:
    return Participant(
        peer_id=_PEER,
        persona="Resident",
        color="p1",
        participant_type="ravn",
        subscribes_to=tuple(subscriptions),
    )


@pytest.mark.asyncio
async def test_relay_uses_participant_declared_subscriptions_and_neutral_evidence() -> None:
    subscriber = _Subscriber()
    send = AsyncMock(return_value="message-1")
    notify = AsyncMock()
    relay = ObservationRelay(
        subscriber,
        participant=lambda: _participant(),
        patterns=["research.*"],
        send_directed=send,
        broadcast_notification=notify,
        payload_preview_chars=2000,
    )

    await relay.start()
    await subscriber.handler(_event("research.completed", {"artifact": "result.md"}))

    target, content, metadata = send.await_args.args
    assert target == _PEER
    assert content.startswith("External observation received:")
    assert "update Mimir" not in content
    assert "report to the room" not in content
    assert metadata["external_observation"] is True
    assert metadata["payload"] == {"artifact": "result.md"}
    assert notify.await_args.args[0]["notificationType"] == "external_observation"


@pytest.mark.asyncio
async def test_relay_ignores_unsubscribed_mesh_and_self_origin_events() -> None:
    subscriber = _Subscriber()
    send = AsyncMock(return_value="message-1")
    relay = ObservationRelay(
        subscriber,
        participant=lambda: _participant(),
        patterns=["*"],
        send_directed=send,
        broadcast_notification=AsyncMock(),
        payload_preview_chars=2000,
    )
    await relay.start()

    await subscriber.handler(_event("delivery.completed"))
    await subscriber.handler(_event("ravn.mesh.activity.peer"))
    await subscriber.handler(_event("research.completed", {"ravn_source": f"ravn:{_PEER}"}))

    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_relay_lifecycle_and_missing_participant_are_safe() -> None:
    subscriber = _Subscriber()
    send = AsyncMock()
    relay = ObservationRelay(
        subscriber,
        participant=lambda: None,
        patterns=["research.*"],
        send_directed=send,
        broadcast_notification=AsyncMock(),
        payload_preview_chars=2000,
    )

    await relay.start()
    assert subscriber.patterns == ["research.*"]
    await subscriber.handler(_event("research.completed"))
    send.assert_not_awaited()
    await relay.stop()
    assert subscriber.subscription.unsubscribed is True
