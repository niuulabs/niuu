"""A workflow run's first session hosts its realm's room and keeps its own agent."""

from __future__ import annotations

from skuld.broker import Broker
from skuld.config import SkuldSettings


def _broker(tmp_path, monkeypatch, **room) -> Broker:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return Broker(
        settings=SkuldSettings(
            session={"id": "coder-session", "workspace_dir": str(tmp_path / "ws")},
            chronicle_watcher_enabled=False,
            room=room,
        )
    )


def test_a_realm_room_shows_every_member_and_keeps_the_hosts_replies_its_own(
    tmp_path, monkeypatch
) -> None:
    broker = _broker(
        tmp_path, monkeypatch, enabled=True, environment_id="workflow-run-1", realm_room=True
    )

    assert broker._room_bridge is not None
    # Members publish under their own sessions: the room takes the whole realm.
    assert broker._room_event_session() is None
    assert broker._room_copies_cli_replies() is False
    # The host keeps its own agent.
    assert broker._is_room_routed_session() is False


def test_any_other_room_shows_only_its_own_session(tmp_path, monkeypatch) -> None:
    broker = _broker(tmp_path, monkeypatch, enabled=True)

    assert broker._room_event_session() == "coder-session"
    assert broker._room_copies_cli_replies() is True
