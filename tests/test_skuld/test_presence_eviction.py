"""Quiet-participant presence and environment-bound capabilities (NIU-1043)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.collaboration.room import ATTENTION_AVAILABLE, ATTENTION_AWAY
from skuld.collaboration_adapter import SkuldCollaborationAdapter
from skuld.config import RoomConfig


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_bridge(
    clock: _Clock | None = None,
) -> tuple[SkuldCollaborationAdapter, MagicMock, _Clock]:
    registry = MagicMock()
    registry.broadcast = AsyncMock()
    clock = clock or _Clock()
    bridge = SkuldCollaborationAdapter(
        config=RoomConfig(enabled=True, participant_colors=["p1", "p2", "p3"]),
        channels=registry,
        emit_frame=registry.broadcast,
        publish_presence_event=AsyncMock(),
        clock=clock,
    )
    return bridge, registry, clock


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.send_text = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# Going quiet changes presence and never ends membership
# ---------------------------------------------------------------------------


async def test_quiet_participant_stays_and_shows_as_away() -> None:
    bridge, registry, clock = _make_bridge()
    await bridge.join_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        role="observer",
        room_id="incident-1",
        heartbeat_ttl_s=60.0,
    )

    clock.advance(61.0)
    quiet = await bridge.mark_quiet_participants()

    assert quiet == ["human:jozef"]
    participant = bridge.participant("human:jozef")
    assert participant is not None
    assert participant.attention_state == ATTENTION_AWAY
    assert participant.room_ids == ("incident-1",)
    frames = [call.args[0] for call in registry.broadcast.call_args_list]
    assert not [frame for frame in frames if frame.get("type") == "participant_left"]
    assert frames[-1]["type"] == "participant_heartbeat"
    assert frames[-1]["participant"]["attention_state"] == ATTENTION_AWAY


async def test_quiet_participant_is_announced_once() -> None:
    bridge, _registry, clock = _make_bridge()
    await bridge.join_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        heartbeat_ttl_s=60.0,
    )
    clock.advance(61.0)
    await bridge.mark_quiet_participants()
    clock.advance(61.0)

    assert await bridge.mark_quiet_participants() == []


async def test_quiet_participant_keeps_the_reply_it_is_owed() -> None:
    bridge, _registry, clock = _make_bridge()
    await bridge.join_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        heartbeat_ttl_s=60.0,
    )
    bridge.set_reply_context("human:jozef", {"case": "c-1"})

    clock.advance(61.0)
    await bridge.mark_quiet_participants()

    assert bridge.pending_reply_peer_ids() == ("human:jozef",)


async def test_only_a_leave_ends_membership() -> None:
    bridge, registry, clock = _make_bridge()
    await bridge.join_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        heartbeat_ttl_s=60.0,
    )
    clock.advance(3600.0)
    await bridge.mark_quiet_participants()

    await bridge.leave_environment("human:jozef", reason="done")

    assert not bridge.has_participant("human:jozef")
    assert registry.broadcast.call_args_list[-1].args[0]["type"] == "participant_left"


async def test_fresh_heartbeat_keeps_a_participant_available() -> None:
    bridge, _registry, clock = _make_bridge()
    await bridge.join_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        heartbeat_ttl_s=60.0,
    )
    clock.advance(50.0)
    await bridge.heartbeat("human:jozef")
    clock.advance(50.0)

    assert await bridge.mark_quiet_participants() == []


async def test_heartbeat_brings_a_quiet_participant_back() -> None:
    bridge, _registry, clock = _make_bridge()
    await bridge.join_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        heartbeat_ttl_s=60.0,
    )
    clock.advance(61.0)
    await bridge.mark_quiet_participants()

    participant = await bridge.heartbeat("human:jozef")

    assert participant is not None
    assert participant.attention_state == ATTENTION_AVAILABLE


async def test_participant_with_live_websocket_never_shows_as_quiet() -> None:
    bridge, _registry, clock = _make_bridge()
    await bridge.register(
        "ravn-1",
        "coder",
        _fake_ws(),
        heartbeat_ttl_s=10.0,
    )
    clock.advance(3600.0)
    assert await bridge.mark_quiet_participants() == []
    assert bridge.has_participant("ravn-1")


async def test_agent_from_another_session_joins_and_leaves_by_role() -> None:
    bridge, _registry, _clock = _make_bridge()
    participant = await bridge.join_environment(
        "coordinator:abc",
        display_name="Ticket coordinator",
        environment_id="cluster-a",
        participant_type="ravn",
        session_id="coordinator-session",
    )

    assert participant.participant_type == "ravn"
    assert participant.session_id == "coordinator-session"

    await bridge.leave_environment("coordinator:abc", reason="run_finished")

    assert not bridge.has_participant("coordinator:abc")


async def test_sweep_lifecycle_start_stop() -> None:
    bridge, _registry, _clock = _make_bridge()
    bridge.start_presence_sweep()
    assert bridge._presence_sweep_task is not None
    await bridge.stop_presence_sweep()
    assert bridge._presence_sweep_task is None


# ---------------------------------------------------------------------------
# Environment-bound human capabilities
# ---------------------------------------------------------------------------


async def test_approver_keeps_powers_when_environment_has_reviewable_actions() -> None:
    bridge, _registry, _clock = _make_bridge()
    meta = await bridge.join_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        role="approver",
        environment_action_authorities=["autonomous", "court_required"],
    )
    assert "approve" in meta.capabilities
    assert "authorize_action" in meta.capabilities


async def test_approver_loses_powers_in_observe_only_environment() -> None:
    bridge, _registry, _clock = _make_bridge()
    meta = await bridge.join_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="printer-cell",
        role="approver",
        environment_action_authorities=["autonomous"],
    )
    assert "approve" not in meta.capabilities
    assert "authorize_action" not in meta.capabilities
    assert "view" in meta.capabilities

    with pytest.raises(PermissionError, match="cannot claim capabilities"):
        await bridge.join_environment(
            "human:other",
            display_name="Other",
            environment_id="printer-cell",
            role="approver",
            capabilities=["view", "approve"],
            environment_action_authorities=["autonomous"],
        )


async def test_owner_keeps_non_approval_powers_in_observe_only_environment() -> None:
    bridge, _registry, _clock = _make_bridge()
    meta = await bridge.join_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="printer-cell",
        role="owner",
        environment_action_authorities=["autonomous"],
    )
    assert "approve" not in meta.capabilities
    assert "change_autonomy" in meta.capabilities
    assert "teach" in meta.capabilities


async def test_legacy_callers_without_environment_info_keep_static_grants() -> None:
    bridge, _registry, _clock = _make_bridge()
    meta = await bridge.join_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        role="approver",
    )
    assert "approve" in meta.capabilities
