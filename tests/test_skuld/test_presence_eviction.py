"""Presence TTL eviction and environment-bound human capabilities (NIU-1043)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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
        publish_presence_event=AsyncMock(),
        clock=clock,
    )
    return bridge, registry, clock


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.send_text = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# TTL eviction
# ---------------------------------------------------------------------------


async def test_expired_participant_is_evicted_with_timeout_event() -> None:
    bridge, registry, clock = _make_bridge()
    await bridge.join_human_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        role="observer",
        heartbeat_ttl_s=60.0,
    )
    assert bridge.has_participant("human:jozef")

    clock.advance(61.0)
    evicted = await bridge.sweep_expired_participants()

    assert evicted == ["human:jozef"]
    assert not bridge.has_participant("human:jozef")
    left = [
        call.args[0]
        for call in registry.broadcast.call_args_list
        if call.args[0].get("type") == "participant_left"
    ]
    assert left and left[0]["reason"] == "heartbeat_timeout"


async def test_fresh_heartbeat_prevents_eviction() -> None:
    bridge, _registry, clock = _make_bridge()
    await bridge.join_human_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        heartbeat_ttl_s=60.0,
    )
    clock.advance(50.0)
    await bridge.heartbeat("human:jozef")
    clock.advance(50.0)

    assert await bridge.sweep_expired_participants() == []
    assert bridge.has_participant("human:jozef")


async def test_participant_with_live_websocket_is_never_evicted() -> None:
    bridge, _registry, clock = _make_bridge()
    await bridge.register(
        "ravn-1",
        "coder",
        _fake_ws(),
        heartbeat_ttl_s=10.0,
    )
    clock.advance(3600.0)
    assert await bridge.sweep_expired_participants() == []
    assert bridge.has_participant("ravn-1")


async def test_rejoin_after_timeout_works() -> None:
    bridge, _registry, clock = _make_bridge()
    await bridge.join_human_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        heartbeat_ttl_s=60.0,
    )
    clock.advance(120.0)
    await bridge.sweep_expired_participants()
    assert not bridge.has_participant("human:jozef")

    meta = await bridge.join_human_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        heartbeat_ttl_s=60.0,
    )
    assert bridge.has_participant("human:jozef")
    assert meta.last_heartbeat_at == clock.now


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
    meta = await bridge.join_human_environment(
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
    meta = await bridge.join_human_environment(
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
        await bridge.join_human_environment(
            "human:other",
            display_name="Other",
            environment_id="printer-cell",
            role="approver",
            capabilities=["view", "approve"],
            environment_action_authorities=["autonomous"],
        )


async def test_owner_keeps_non_approval_powers_in_observe_only_environment() -> None:
    bridge, _registry, _clock = _make_bridge()
    meta = await bridge.join_human_environment(
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
    meta = await bridge.join_human_environment(
        "human:jozef",
        display_name="Jozef",
        environment_id="cluster-a",
        role="approver",
    )
    assert "approve" in meta.capabilities
