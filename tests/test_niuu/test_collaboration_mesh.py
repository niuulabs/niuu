"""Tests for collaboration events carried over the Niuu mesh."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from niuu.collaboration.mesh import MeshCollaborationBridge, mesh_peer_id
from sleipnir.domain.events import SleipnirEvent


class _Subscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class _Subscriber:
    def __init__(self) -> None:
        self.patterns = None
        self.handler = None
        self.subscription = _Subscription()

    async def subscribe(self, patterns, handler):
        self.patterns = list(patterns)
        self.handler = handler
        return self.subscription


def _event(
    events: list[dict],
    *,
    source: str = "ravn:peer-1",
    session_id: str = "session-1",
    ravn_event: dict | None = None,
) -> SleipnirEvent:
    return SleipnirEvent(
        event_type="ravn.mesh.activity.peer_1",
        source=source,
        payload={
            "collaboration_events": events,
            "ravn_event": ravn_event or {},
            "ravn_session_id": session_id,
        },
        summary="activity",
        urgency=0.2,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=session_id,
    )


@pytest.mark.asyncio
async def test_mesh_bridge_forwards_projected_events_without_ravn_interpretation() -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    register = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=register,
        has_participant=lambda _peer: False,
        session_id="session-1",
        environment_id="cluster-a",
    )
    await bridge.start()

    projected = {
        "kind": "outcome",
        "persona": "Reviewer",
        "eventType": "review.completed",
        "fields": {"verdict": "approve"},
    }
    await subscriber.handler(_event([projected]))

    register.assert_awaited_once_with(peer_id="peer-1", persona="Reviewer", display_name="Reviewer")
    handle.assert_awaited_once_with(
        "peer-1", {"type": "collaboration.events", "events": [projected]}
    )


@pytest.mark.asyncio
async def test_mesh_bridge_filters_session_and_forwards_all_projected_events() -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=AsyncMock(),
        has_participant=lambda _peer: True,
        session_id="session-1",
    )
    await bridge.start()

    await subscriber.handler(
        _event([{"kind": "message", "content": "wrong"}], session_id="session-2")
    )
    await subscriber.handler(
        _event(
            [
                {"kind": "usage", "usage": {"model": "codex"}},
                {"kind": "activity", "activityType": "thinking"},
            ]
        )
    )

    handle.assert_awaited_once()
    assert handle.await_args.args[1]["events"] == [
        {"kind": "usage", "usage": {"model": "codex"}},
        {"kind": "activity", "activityType": "thinking"},
    ]


@pytest.mark.asyncio
async def test_mesh_bridge_ignores_unprojected_events_and_stops_cleanly() -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=AsyncMock(),
        has_participant=lambda _peer: True,
    )
    await bridge.start()
    event = _event([])
    event.payload.pop("collaboration_events")
    await subscriber.handler(event)
    handle.assert_not_awaited()

    await bridge.stop()
    assert subscriber.subscription.unsubscribed is True


@pytest.mark.asyncio
async def test_mesh_bridge_drops_collaboration_copy_already_delivered_directly() -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    register = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=register,
        has_participant=lambda _peer: False,
        session_id="session-1",
    )
    await bridge.start()

    await subscriber.handler(
        _event(
            [{"kind": "outcome", "eventType": "review.completed"}],
            ravn_event={"collaboration_routing_only": True},
        )
    )

    register.assert_not_awaited()
    handle.assert_not_awaited()


def test_mesh_peer_identity_uses_outer_mesh_publisher() -> None:
    assert mesh_peer_id(_event([], source="ravn:stable-peer")) == "stable-peer"
