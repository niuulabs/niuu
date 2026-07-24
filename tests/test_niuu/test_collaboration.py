"""Contract tests for the shared collaboration room."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from niuu.collaboration import CollaborationRoom


def _room(**kwargs) -> CollaborationRoom:
    return CollaborationRoom(
        participant_colors=("p1", "p2"),
        environment_id="cluster-a",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_room_owns_membership_presence_and_wire_state() -> None:
    broadcast = AsyncMock()
    publish = AsyncMock()
    room = _room(broadcast=broadcast, publish=publish, clock=lambda: 100.0)

    participant = await room.register_agent(
        "ravn-1",
        "Resident",
        capabilities=("inspect",),
        room_ids=("incident-1",),
    )

    assert participant.environment_id == "cluster-a"
    assert len(room.participants) == 1
    assert room.get_room_state_event()["participants"][0]["peer_id"] == "ravn-1"
    assert broadcast.await_args.args[0]["type"] == "participant_joined"
    assert publish.await_args.args[0].event_type == "participant.joined"


@pytest.mark.asyncio
async def test_room_routes_opaque_reply_context_through_delivery_port() -> None:
    deliver = AsyncMock(return_value=True)
    room = _room(deliver=deliver)
    room.set_reply_context("ravn-1", {"case_id": "case-1"})

    delivered = await room.route_directed_message(
        "ravn-1", "Approved", metadata={"operator": "jozef"}
    )

    assert delivered is True
    assert room.pending_reply_peer_ids() == ()
    assert deliver.await_args.args == (
        "ravn-1",
        {
            "type": "directed_message",
            "content": "Approved",
            "metadata": {"case_id": "case-1", "operator": "jozef"},
        },
    )


@pytest.mark.asyncio
async def test_failed_delivery_preserves_reply_context() -> None:
    room = _room(deliver=AsyncMock(return_value=False))
    room.set_reply_context("ravn-1", {"case_id": "case-1"})

    assert await room.route_directed_message("ravn-1", "hello") is False
    assert room.pending_reply_peer_ids() == ("ravn-1",)


@pytest.mark.asyncio
async def test_room_records_and_replays_huddle_without_transport_dependencies() -> None:
    publish = AsyncMock()
    recorded = datetime(2026, 7, 21, tzinfo=UTC)
    room = _room(publish=publish, recorded_at=lambda: recorded)
    await room.register_agent("ravn-1", "Resident")

    await room.open_environment_huddle(room_id="incident-1", purpose="diagnose")
    await room.record_huddle_message(
        room_id="incident-1",
        environment_id="cluster-a",
        message_id="message-1",
        participant_id="ravn-1",
        role="assistant",
        content="Investigating",
    )
    closed = await room.close_environment_huddle(room_id="incident-1")

    replay = room.replay_huddle("incident-1")
    assert [event["sequence"] for event in replay] == list(range(1, len(replay) + 1))
    assert "## ravn-1\nInvestigating" in room.build_huddle_transcript("incident-1")
    assert closed["transcriptRef"] == "huddles/cluster-a/incident-1.md"
    transcript_event = next(
        call.args[0]
        for call in publish.await_args_list
        if call.args[0].event_type == "room.transcript.recorded"
    )
    assert "Investigating" in transcript_event.payload["transcript_content"]


@pytest.mark.asyncio
async def test_presence_expiry_respects_live_transport_connection() -> None:
    now = 100.0
    connected = {"connected"}
    room = _room(clock=lambda: now, is_connected=lambda peer: peer in connected)
    await room.register_agent("connected", "Connected", heartbeat_ttl_s=10.0)
    await room.register_agent("gone", "Gone", heartbeat_ttl_s=10.0)

    now = 111.0
    expired = await room.sweep_expired_participants()

    assert expired == ["gone"]
    assert room.has_participant("connected")
    assert not room.has_participant("gone")


@pytest.mark.asyncio
async def test_human_authority_is_bounded_by_environment_actions() -> None:
    room = _room()

    participant = await room.join_human_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        role="approver",
        environment_action_authorities=("autonomous",),
    )

    assert "approve" not in participant.capabilities
    assert participant.capabilities == ("view", "reply")
