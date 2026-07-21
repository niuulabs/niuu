"""Tests for replayable Environment huddles in Skuld rooms."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from skuld.collaboration_adapter import SkuldCollaborationAdapter
from skuld.config import RoomConfig
from sleipnir.domain import registry as event_registry


def _make_bridge(publish_presence_event=None):
    channels = MagicMock()
    channels.broadcast = AsyncMock()
    published: list = []

    async def publish(event):
        published.append(event)

    bridge = SkuldCollaborationAdapter(
        config=RoomConfig(enabled=True, participant_colors=["p1", "p2", "p3"]),
        channels=channels,
        publish_presence_event=publish_presence_event or publish,
        environment_id="cluster-a",
    )
    return bridge, channels, published


class _Mimir:
    def __init__(self) -> None:
        self.pages: dict[str, str] = {}

    async def upsert_page(self, path: str, content: str, mimir=None, meta=None) -> None:
        self.pages[path] = content


async def test_huddle_open_records_context_and_canonical_events() -> None:
    bridge, channels, published = _make_bridge()

    opened = await bridge.open_environment_huddle(
        room_id="huddle-1",
        purpose="Review crashing pod",
        environment_id="cluster-a",
        root_correlation_id="root-1",
        active_state={"state": "investigating"},
        signal_refs=["signal-1"],
        judgment_refs=["judgment-1"],
        action_refs=["action-1"],
        participants=["human:owner", "valkyrie:k8s"],
    )

    assert opened["type"] == "room_opened"
    replay = bridge.replay_huddle("huddle-1")
    assert [event["type"] for event in replay] == [
        "room_opened",
        "room_context_snapshot",
    ]
    assert replay[0]["sequence"] < replay[1]["sequence"]
    assert replay[1]["activeState"] == {"state": "investigating"}
    assert [event.event_type for event in published] == [
        event_registry.ROOM_OPENED,
        event_registry.ROOM_CONTEXT_SNAPSHOT_RECORDED,
    ]
    channels.broadcast.assert_awaited()


async def test_late_join_replay_orders_human_and_mesh_messages() -> None:
    bridge, _, published = _make_bridge()
    await bridge.open_environment_huddle(
        room_id="huddle-2",
        purpose="Action review",
        root_correlation_id="root-2",
    )
    human = await bridge.join_human_environment(
        "human:approver",
        display_name="Approver",
        environment_id="cluster-a",
        role="approver",
        room_id="huddle-2",
    )
    await bridge.register_mesh_peer(
        "valkyrie:k8s",
        "K8s Valkyrie",
        participant_kind="valkyrie",
        environment_id="cluster-a",
        room_ids=["huddle-2"],
    )

    await bridge.record_huddle_message(
        room_id="huddle-2",
        environment_id=human.environment_id,
        message_id="human-msg",
        participant_id=human.peer_id,
        role="user",
        content="Please inspect the pod first.",
        thread_id="thread-2",
        metadata={"root_correlation_id": "root-2"},
    )
    await bridge.handle_collaboration_frame(
        "valkyrie:k8s",
        {
            "kind": "message",
            "sourceEventType": "response",
            "content": "Inspection complete.",
            "metadata": {"thread_id": "thread-2"},
        },
    )

    replay = bridge.replay_huddle("huddle-2", from_sequence=2)
    messages = [event for event in replay if event["type"] == "room_message"]
    assert [event["participantId"] for event in messages] == [
        "human:approver",
        "valkyrie:k8s",
    ]
    assert [event["threadId"] for event in messages] == ["thread-2", "thread-2"]
    assert all(event.event_type != event_registry.PARTICIPANT_JOINED for event in published[:2])
    assert published[-2].event_type == event_registry.ROOM_MESSAGE_RECORDED
    assert published[-1].event_type == event_registry.ROOM_MESSAGE_RECORDED


async def test_huddle_close_publishes_transcript_for_archival() -> None:
    """Closing a huddle publishes the rendered transcript so the resident
    daemon's archiver can persist it to Mimir (the broker has no Mimir)."""
    bridge, channels, published = _make_bridge()
    await bridge.open_environment_huddle(
        room_id="huddle-3",
        purpose="Closeout",
        environment_id="cluster-a",
        root_correlation_id="root-3",
    )
    await bridge.record_huddle_message(
        room_id="huddle-3",
        environment_id="cluster-a",
        message_id="msg-1",
        participant_id="human:owner",
        role="user",
        content="This incident is resolved.",
    )

    closed = await bridge.close_environment_huddle(
        room_id="huddle-3",
        reason="resolved",
        summary="Incident resolved.",
    )

    assert closed["type"] == "room_closed"
    assert closed["transcriptRef"] == "huddles/cluster-a/huddle-3.md"
    transcript = published[-2]
    assert transcript.event_type == event_registry.ROOM_TRANSCRIPT_RECORDED
    assert transcript.payload["transcript_ref"] == "huddles/cluster-a/huddle-3.md"
    assert "This incident is resolved." in transcript.payload["transcript_content"]
    assert published[-1].event_type == event_registry.ROOM_CLOSED
    assert channels.broadcast.await_args.args[0]["type"] == "room_closed"


async def test_archiver_persists_published_transcript_to_mimir() -> None:
    """End to end across the seam: huddle close -> event -> Mimir page."""
    from ravn.huddle_archiver import HuddleTranscriptArchiver
    from sleipnir.adapters.in_process import InProcessBus

    bus = InProcessBus()
    mimir = _Mimir()
    archiver = HuddleTranscriptArchiver(subscriber=bus, mimir=mimir)
    await archiver.start()

    bridge, _channels, _published = _make_bridge(publish_presence_event=bus.publish)
    await bridge.open_environment_huddle(
        room_id="huddle-4",
        purpose="Closeout",
        environment_id="cluster-a",
        root_correlation_id="root-4",
    )
    await bridge.record_huddle_message(
        room_id="huddle-4",
        environment_id="cluster-a",
        message_id="msg-1",
        participant_id="human:owner",
        role="user",
        content="Archive me.",
    )
    await bridge.close_environment_huddle(room_id="huddle-4")
    await bus.flush()

    assert archiver.archived_paths() == ["huddles/cluster-a/huddle-4.md"]
    assert "Archive me." in mimir.pages["huddles/cluster-a/huddle-4.md"]
    await archiver.stop()
