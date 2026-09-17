"""The room → OpenClaw session bridge.

The HTTP calls are the seam; these cover the translation decisions, which are where a room
either reads correctly on a phone or silently lies about who said what.
"""

from __future__ import annotations

from pathlib import Path

from ravn.adapters.channels import openclaw_room as room


class TestSessionKeyGrammar:
    def test_a_room_key_round_trips(self) -> None:
        assert room.room_name_from_key(room.room_session_key("backstage")) == "backstage"

    def test_an_agent_session_is_not_a_room(self) -> None:
        """`agent:travis:lexi:x` is a named channel of one agent, not a room."""
        assert room.room_name_from_key("agent:travis:lexi:x") is None

    def test_a_malformed_key_is_not_a_room(self) -> None:
        assert room.room_name_from_key("") is None
        assert room.room_name_from_key("backstage") is None
        assert room.room_name_from_key("agent::main") is None


class TestDiscovery:
    def test_rooms_are_read_from_their_definitions(self, tmp_path: Path) -> None:
        _write_room(tmp_path, "backstage", port=7503)
        _write_room(tmp_path, "standup", port=7504)

        refs = room.discover_rooms(tmp_path)

        assert [(r.name, r.port) for r in refs] == [("backstage", 7503), ("standup", 7504)]

    def test_one_unreadable_room_does_not_hide_the_others(self, tmp_path: Path) -> None:
        _write_room(tmp_path, "good", port=7503)
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / "room.yaml").write_text("name: broken\n", encoding="utf-8")  # no port

        assert [r.name for r in room.discover_rooms(tmp_path)] == ["good"]

    def test_a_missing_rooms_directory_is_simply_no_rooms(self, tmp_path: Path) -> None:
        assert room.discover_rooms(tmp_path / "nope") == []

    def test_a_directory_without_a_definition_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "scratch").mkdir()

        assert room.discover_rooms(tmp_path) == []


class TestTurnTranslation:
    def test_a_turn_carries_its_speaker(self) -> None:
        """Verbatim shape from the backstage broker."""
        turn = {
            "id": "t1",
            "role": "assistant",
            "content": "The broker is on 7503.",
            "participant_id": "neo",
            "participant_meta": {
                "peer_id": "neo",
                "persona": "neo",
                "color": "p5",
                "participant_type": "ravn",
                "display_name": "neo",
            },
        }

        message = room.turn_to_message(turn)

        assert message is not None
        assert message["role"] == "assistant"
        assert message["sender"] == {
            "id": "neo",
            "displayName": "neo",
            "kind": "agent",
            "color": "p5",
            "persona": "neo",
        }
        assert message["blocks"] == [{"type": "text", "text": "The broker is on 7503."}]

    def test_a_human_turn_is_a_user_message(self) -> None:
        turn = {
            "id": "t1",
            "role": "user",
            "content": "who is here?",
            "participant_id": "human:damien",
            "participant_meta": {"participant_type": "human", "display_name": "Damien"},
        }

        message = room.turn_to_message(turn)

        assert message is not None
        assert message["role"] == "user"
        assert message["sender"]["kind"] == "human"

    def test_a_ravn_participant_type_is_an_agent(self) -> None:
        """The room says `ravn`; the protocol says agent|human. Only `human` is special."""
        sender = room.participant_from_turn(
            {"participant_id": "neo", "participant_meta": {"participant_type": "ravn"}}
        )

        assert sender is not None
        assert sender["kind"] == "agent"

    def test_an_empty_turn_is_dropped(self) -> None:
        assert room.turn_to_message({"id": "t", "role": "assistant", "content": "   "}) is None
        assert room.turn_to_message({"id": "t", "role": "assistant"}) is None

    def test_a_turn_with_no_speaker_still_renders(self) -> None:
        """Attribution is better-effort: a turn without it is worth more than no turn."""
        message = room.turn_to_message({"id": "t", "role": "assistant", "content": "hi"})

        assert message is not None
        assert "sender" not in message


class TestMentionResolution:
    PEERS = {"travis", "neo", "human:damien"}

    def test_a_handle_resolves(self) -> None:
        assert room._resolve_mentions("@neo check the port", self.PEERS) == ["neo"]

    def test_a_human_answers_to_their_bare_name(self) -> None:
        assert room._resolve_mentions("@damien look", self.PEERS) == ["human:damien"]

    def test_an_unknown_handle_is_not_a_recipient(self) -> None:
        assert room._resolve_mentions("@nobody hello", self.PEERS) == []

    def test_a_handle_inside_code_is_not_an_address(self) -> None:
        """Pasting a command must not invoke whoever appears in it."""
        assert room._resolve_mentions("run `ping @neo` please", self.PEERS) == []
        assert room._resolve_mentions("```\n@neo\n```", self.PEERS) == []

    def test_several_handles_keep_their_order_and_dedupe(self) -> None:
        assert room._resolve_mentions("@neo @travis @neo", self.PEERS) == ["neo", "travis"]


class TestSessionRow:
    def test_the_row_names_the_room_as_its_agent(self) -> None:
        """The client filters channel visibility by agent id — a room needs a real one."""
        ref = room.RoomRef(name="backstage", host="127.0.0.1", port=7503)

        row = room.session_row(
            ref,
            participants=[{"id": "travis"}, {"id": "neo"}],
            last_message="hello",
            updated_at_ms=1_700_000_000_000,
            live=True,
        )

        assert row["key"] == "agent:backstage:main"
        assert row["agentId"] == "backstage"
        assert row["status"] == "idle"
        assert len(row["participants"]) == 2

    def test_a_stopped_room_is_listed_as_offline_not_hidden(self) -> None:
        """Hiding a stopped room would read as data loss; it has real history."""
        ref = room.RoomRef(name="backstage", host="127.0.0.1", port=7503)

        row = room.session_row(
            ref, participants=[], last_message=None, updated_at_ms=None, live=False
        )

        assert row["status"] == "offline"
        assert row["hidden"] is False


def _write_room(rooms_dir: Path, name: str, *, port: int) -> None:
    directory = rooms_dir / name
    directory.mkdir(parents=True)
    (directory / "room.yaml").write_text(
        f"name: {name}\nenvironment_id: {name}\nhost: 127.0.0.1\nport: {port}\n",
        encoding="utf-8",
    )
