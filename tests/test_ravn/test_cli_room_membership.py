"""Tests for room membership (``ravn join`` / ``leave``) and mention routing.

Daemon processes are never spawned: ``_spawn_member`` and the registration
probe are the seams, so these cover the decisions — identity resolution,
handle collisions, routing — rather than process management.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ravn.cli import room as room_mod
from ravn.cli.commands import app
from ravn.cli.room import RoomDef, room_app

runner = CliRunner()


@pytest.fixture
def rooms_dir(tmp_path: Path) -> Path:
    return tmp_path / "rooms"


@pytest.fixture
def live_room(rooms_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A created, running room whose members register successfully."""
    state: dict = {"spawned": [], "registered": True, "broker_pid": 4242, "member_pid": 5150}

    monkeypatch.setattr(room_mod, "_spawn_broker", lambda rd, d: int(state["broker_pid"]))
    monkeypatch.setattr(room_mod, "_broker_responding", lambda rd: True)
    monkeypatch.setattr(room_mod, "port_free", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(room_mod, "stop_pids", lambda pids, **kw: None)
    monkeypatch.setattr(
        room_mod, "is_alive", lambda pid: pid in (state["broker_pid"], state["member_pid"])
    )

    def _spawn_member(rd: RoomDef, d: Path, handle: str, persona: str, cfg: Path) -> int:
        state["spawned"].append((handle, persona))
        return int(state["member_pid"])

    monkeypatch.setattr(room_mod, "_spawn_member", _spawn_member)
    monkeypatch.setattr(
        room_mod, "_await_member_registration", lambda rd, handle, pid: state["registered"]
    )

    result = runner.invoke(
        room_app, ["create", "desk", "--rooms-dir", str(rooms_dir), "--port", "7500"]
    )
    assert result.exit_code == 0, result.output
    return state


def _join(rooms_dir: Path, *args: str):
    return runner.invoke(app, ["join", "--rooms-dir", str(rooms_dir), *args])


class TestJoin:
    def test_persona_becomes_the_default_handle(self, rooms_dir: Path, live_room: dict) -> None:
        result = _join(rooms_dir, "--persona", "reviewer", "--room", "desk")

        assert result.exit_code == 0, result.output
        assert live_room["spawned"] == [("reviewer", "reviewer")]
        assert room_mod._load_member_state("desk", rooms_dir, "reviewer") is not None

    def test_as_overrides_the_handle(self, rooms_dir: Path, live_room: dict) -> None:
        result = _join(rooms_dir, "--persona", "coder", "--room", "desk", "--as", "builder")

        assert result.exit_code == 0, result.output
        assert live_room["spawned"] == [("builder", "coder")]

    def test_member_config_targets_the_rooms_broker(self, rooms_dir: Path, live_room: dict) -> None:
        import yaml

        _join(rooms_dir, "--persona", "reviewer", "--room", "desk")

        config = yaml.safe_load(
            room_mod._member_config_path("desk", rooms_dir, "reviewer").read_text(encoding="utf-8")
        )
        assert config["skuld"]["broker_url"] == "ws://127.0.0.1:7500/ws/ravn"
        assert config["mesh"]["own_peer_id"] == "reviewer"

    def test_unknown_persona_fails_before_spawning(self, rooms_dir: Path, live_room: dict) -> None:
        """Identity is resolved up front so failures surface here, not in a log."""
        result = _join(rooms_dir, "--persona", "does-not-exist", "--room", "desk")

        assert result.exit_code == 2
        assert live_room["spawned"] == []

    def test_duplicate_handle_is_refused(self, rooms_dir: Path, live_room: dict) -> None:
        _join(rooms_dir, "--persona", "reviewer", "--room", "desk")
        result = _join(rooms_dir, "--persona", "reviewer", "--room", "desk")

        assert result.exit_code == 1
        assert "already in" in result.output

    def test_force_replaces_an_existing_member(self, rooms_dir: Path, live_room: dict) -> None:
        _join(rooms_dir, "--persona", "reviewer", "--room", "desk")
        result = _join(rooms_dir, "--persona", "reviewer", "--room", "desk", "--force")

        assert result.exit_code == 0, result.output
        assert len(live_room["spawned"]) == 2

    def test_joining_a_stopped_room_fails(self, rooms_dir: Path, live_room: dict) -> None:
        runner.invoke(room_app, ["stop", "desk", "--rooms-dir", str(rooms_dir)])

        result = _join(rooms_dir, "--persona", "reviewer", "--room", "desk")

        assert result.exit_code == 1
        assert "not running" in result.output

    def test_failed_registration_cleans_up(self, rooms_dir: Path, live_room: dict) -> None:
        """A member that never registers must not be left recorded as joined."""
        live_room["registered"] = False

        result = _join(rooms_dir, "--persona", "reviewer", "--room", "desk")

        assert result.exit_code == 1
        assert "did not register" in result.output
        assert room_mod._load_member_state("desk", rooms_dir, "reviewer") is None

    def test_no_persona_and_no_profile_is_an_error(self, rooms_dir: Path, live_room: dict) -> None:
        result = _join(rooms_dir, "--room", "desk")

        assert result.exit_code == 2
        assert "Nothing to join as" in result.output

    def test_single_room_is_inferred(self, rooms_dir: Path, live_room: dict) -> None:
        result = _join(rooms_dir, "--persona", "reviewer")

        assert result.exit_code == 0, result.output

    def test_ambiguous_room_requires_an_explicit_choice(
        self, rooms_dir: Path, live_room: dict
    ) -> None:
        runner.invoke(
            room_app,
            ["create", "lab", "--rooms-dir", str(rooms_dir), "--port", "7502", "--no-start"],
        )

        result = _join(rooms_dir, "--persona", "reviewer")

        assert result.exit_code == 2
        assert "pass --room" in result.output


class TestLeave:
    def test_leave_removes_the_member(self, rooms_dir: Path, live_room: dict) -> None:
        _join(rooms_dir, "--persona", "reviewer", "--room", "desk")

        result = runner.invoke(
            app, ["leave", "--as", "reviewer", "--room", "desk", "--rooms-dir", str(rooms_dir)]
        )

        assert result.exit_code == 0, result.output
        assert room_mod._load_member_state("desk", rooms_dir, "reviewer") is None

    def test_leaving_a_non_member_fails(self, rooms_dir: Path, live_room: dict) -> None:
        result = runner.invoke(
            app, ["leave", "--as", "ghost", "--room", "desk", "--rooms-dir", str(rooms_dir)]
        )

        assert result.exit_code == 1
        assert "not a member" in result.output


class TestMembersListing:
    def test_runtime_files_are_not_mistaken_for_members(
        self, rooms_dir: Path, live_room: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A member's queue journal must never appear as a second member."""
        _join(rooms_dir, "--persona", "reviewer", "--room", "desk")
        monkeypatch.setattr(room_mod, "_fetch_participants", lambda rd: [])

        assert room_mod._list_member_handles("desk", rooms_dir) == ["reviewer"]

    def test_members_reports_process_and_room_state(
        self, rooms_dir: Path, live_room: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _join(rooms_dir, "--persona", "reviewer", "--room", "desk")

        class _Response:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"participants": [{"peer_id": "reviewer"}]}

        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Response())

        result = runner.invoke(
            room_app, ["members", "--room", "desk", "--rooms-dir", str(rooms_dir)]
        )

        assert result.exit_code == 0, result.output
        assert "reviewer" in result.output
        assert "running" in result.output

    def test_empty_room_says_so(self, rooms_dir: Path, live_room: dict) -> None:
        result = runner.invoke(
            room_app, ["members", "--room", "desk", "--rooms-dir", str(rooms_dir)]
        )

        assert result.exit_code == 0
        assert "No ravens joined" in result.output


class TestPostRouting:
    @pytest.fixture
    def posted(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict]]:
        calls: list[tuple[str, str, dict]] = []
        monkeypatch.setattr(
            room_mod,
            "_post",
            lambda base, path, payload: (calls.append((base, path, payload)), {})[1],
        )
        monkeypatch.setattr(
            room_mod,
            "_fetch_participants",
            lambda rd: [
                {"peer_id": "reviewer"},
                {"peer_id": "builder"},
                {"peer_id": "human:jozef"},
            ],
        )
        monkeypatch.setattr(room_mod, "_last_speaker", lambda rd, exclude: "")
        return calls

    def _post_cmd(self, rooms_dir: Path, body: str, *extra: str):
        return runner.invoke(
            room_app,
            [
                "post",
                body,
                "--as",
                "human:jozef",
                "--room",
                "desk",
                "--rooms-dir",
                str(rooms_dir),
                *extra,
            ],
        )

    def test_explicit_target_is_delivered_directly(
        self, rooms_dir: Path, live_room: dict, posted: list
    ) -> None:
        result = self._post_cmd(rooms_dir, "take a look", "--to", "reviewer")

        assert result.exit_code == 0, result.output
        _, path, payload = posted[0]
        assert path == "/api/room/direct"
        assert payload["target_peer_id"] == "reviewer"

    def test_untagged_message_falls_back_to_the_last_speaker(
        self, rooms_dir: Path, live_room: dict, posted: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(room_mod, "_last_speaker", lambda rd, exclude: "reviewer")

        result = self._post_cmd(rooms_dir, "carry on")

        assert result.exit_code == 0, result.output
        assert posted[0][2]["target_peer_id"] == "reviewer"

    def test_no_target_becomes_commentary_without_transport_delivery(
        self, rooms_dir: Path, live_room: dict, posted: list
    ) -> None:
        result = self._post_cmd(rooms_dir, "thinking out loud")

        assert result.exit_code == 0, result.output
        _, path, payload = posted[0]
        assert path == "/api/room/message"
        assert payload["deliver_to_transport"] is False

    def test_unknown_target_is_refused(
        self, rooms_dir: Path, live_room: dict, posted: list
    ) -> None:
        """A typo must not be silently delivered somewhere else."""
        result = self._post_cmd(rooms_dir, "hello", "--to", "reviwer")

        assert result.exit_code == 1
        assert "is not in room" in result.output
        assert posted == []

    def test_a_non_participant_cannot_post(
        self, rooms_dir: Path, live_room: dict, posted: list
    ) -> None:
        result = runner.invoke(
            room_app,
            [
                "post",
                "hello",
                "--as",
                "human:stranger",
                "--room",
                "desk",
                "--rooms-dir",
                str(rooms_dir),
            ],
        )

        assert result.exit_code == 1
        assert "is not in room" in result.output
        assert posted == []


class TestHistoryHelpers:
    def test_turns_from_a_bare_list(self) -> None:
        assert room_mod._history_turns([{"id": "1"}]) == [{"id": "1"}]

    @pytest.mark.parametrize("key", ["turns", "messages", "history"])
    def test_turns_from_a_wrapped_payload(self, key: str) -> None:
        assert room_mod._history_turns({key: [{"id": "1"}]}) == [{"id": "1"}]

    def test_unknown_shape_yields_nothing(self) -> None:
        assert room_mod._history_turns({"unexpected": 1}) == []
        assert room_mod._history_turns("nonsense") == []

    def test_turn_formatting_truncates_long_bodies(self) -> None:
        line = room_mod._format_turn({"participant_id": "reviewer", "content": "x" * 500})

        assert line.startswith("reviewer")
        assert line.endswith("…")
        assert len(line) < 200

    def test_turn_formatting_collapses_newlines(self) -> None:
        line = room_mod._format_turn({"participant_id": "r", "content": "one\ntwo"})

        assert "\n" not in line
        assert "one two" in line


class TestFetchParticipants:
    def test_unreachable_room_fails_cleanly(
        self, rooms_dir: Path, live_room: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dead broker is an operator-facing error, never a stack trace."""
        import httpx
        import typer

        def _raise(*args, **kwargs):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "get", _raise)
        room_def = room_mod._require_room_def("desk", rooms_dir)

        with pytest.raises(typer.Exit) as exc:
            room_mod._fetch_participants(room_def)

        assert exc.value.exit_code == 1

    def test_returns_the_roster(
        self, rooms_dir: Path, live_room: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        class _Response:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"participants": [{"peer_id": "reviewer"}]}

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Response())
        room_def = room_mod._require_room_def("desk", rooms_dir)

        assert room_mod._fetch_participants(room_def) == [{"peer_id": "reviewer"}]


class TestLastSpeaker:
    def _room_def(self) -> RoomDef:
        return RoomDef("desk", "desk", "127.0.0.1", 7500, "")

    def _history(self, monkeypatch: pytest.MonkeyPatch, turns: list[dict]) -> None:
        import httpx

        class _Response:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"turns": turns}

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Response())

    def test_picks_the_most_recent_other_speaker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._history(
            monkeypatch,
            [
                {"participant_id": "human:jozef"},
                {"participant_id": "reviewer"},
                {"participant_id": "builder"},
            ],
        )

        assert room_mod._last_speaker(self._room_def(), exclude="human:jozef") == "builder"

    def test_skips_the_author(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._history(
            monkeypatch,
            [{"participant_id": "reviewer"}, {"participant_id": "human:jozef"}],
        )

        assert room_mod._last_speaker(self._room_def(), exclude="human:jozef") == "reviewer"

    def test_empty_history_yields_nobody(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._history(monkeypatch, [])

        assert room_mod._last_speaker(self._room_def(), exclude="human:jozef") == ""

    def test_unreachable_history_yields_nobody(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A default recipient is a convenience; losing it must not fail the post."""
        import httpx

        def _raise(*args, **kwargs):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "get", _raise)

        assert room_mod._last_speaker(self._room_def(), exclude="human:jozef") == ""


class TestTail:
    def _history(self, monkeypatch: pytest.MonkeyPatch, turns: list[dict]) -> None:
        import httpx

        class _Response:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"turns": turns}

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Response())

    def test_prints_recent_turns(
        self, rooms_dir: Path, live_room: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._history(
            monkeypatch,
            [
                {"id": "1", "participant_id": "human:jozef", "content": "@reviewer look"},
                {"id": "2", "participant_id": "reviewer", "content": "on it"},
            ],
        )

        result = runner.invoke(room_app, ["tail", "--room", "desk", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 0, result.output
        assert "@reviewer look" in result.output
        assert "on it" in result.output

    def test_unreachable_room_fails_cleanly(
        self, rooms_dir: Path, live_room: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        def _raise(*args, **kwargs):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "get", _raise)

        result = runner.invoke(room_app, ["tail", "--room", "desk", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 1
        assert "unreachable" in result.output

    def test_follow_prints_only_new_turns_then_stops(
        self, rooms_dir: Path, live_room: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Following must not reprint history on every poll."""
        batches = [
            [{"id": "1", "participant_id": "human:jozef", "content": "first"}],
            [
                {"id": "1", "participant_id": "human:jozef", "content": "first"},
                {"id": "2", "participant_id": "reviewer", "content": "second"},
            ],
        ]
        calls = {"n": 0}

        def _fetch_turns(*args, **kwargs):
            index = min(calls["n"], len(batches) - 1)
            calls["n"] += 1
            return batches[index]

        import httpx

        class _Response:
            def __init__(self, turns: list[dict]) -> None:
                self._turns = turns

            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"turns": self._turns}

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Response(_fetch_turns()))
        monkeypatch.setattr(room_mod, "_TAIL_POLL_INTERVAL_S", 0.01)

        def _sleep_then_interrupt(_seconds: float) -> None:
            if calls["n"] >= 2:
                raise KeyboardInterrupt
            return None

        monkeypatch.setattr(room_mod.time, "sleep", _sleep_then_interrupt)

        result = runner.invoke(
            room_app, ["tail", "--room", "desk", "--rooms-dir", str(rooms_dir), "--follow"]
        )

        assert result.exit_code == 0, result.output
        assert result.output.count("first") == 1
        assert "second" in result.output


class TestAwaitRegistration:
    def test_returns_false_when_the_process_dies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(room_mod, "is_alive", lambda pid: False)
        room_def = RoomDef("desk", "desk", "127.0.0.1", 7500, "")

        assert room_mod._await_member_registration(room_def, "reviewer", 1234) is False

    def test_returns_true_once_the_handle_appears(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        class _Response:
            status_code = 200

            def json(self) -> dict:
                return {"participants": [{"peer_id": "reviewer"}]}

        monkeypatch.setattr(room_mod, "is_alive", lambda pid: True)
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Response())
        room_def = RoomDef("desk", "desk", "127.0.0.1", 7500, "")

        assert room_mod._await_member_registration(room_def, "reviewer", 1234) is True


class TestMemberPortAllocation:
    def test_ports_are_persisted_not_derived_from_sort_order(
        self, rooms_dir: Path, live_room: dict
    ) -> None:
        """A handle's roster position shifts; its ports must not move with it."""
        _join(rooms_dir, "--persona", "reviewer", "--room", "desk")
        reviewer = room_mod._load_member_state("desk", rooms_dir, "reviewer")
        assert reviewer is not None
        first_ports = (reviewer["pub_port"], reviewer["rep_port"])

        # 'builder' sorts before 'reviewer' — a derived index would move it.
        _join(rooms_dir, "--persona", "coder", "--room", "desk", "--as", "builder")

        reviewer_after = room_mod._load_member_state("desk", rooms_dir, "reviewer")
        assert reviewer_after is not None
        assert (reviewer_after["pub_port"], reviewer_after["rep_port"]) == first_ports

    def test_members_do_not_share_ports(self, rooms_dir: Path, live_room: dict) -> None:
        _join(rooms_dir, "--persona", "reviewer", "--room", "desk")
        _join(rooms_dir, "--persona", "coder", "--room", "desk", "--as", "builder")

        ports = {
            room_mod._load_member_state("desk", rooms_dir, handle)["pub_port"]
            for handle in ("reviewer", "builder")
        }
        assert len(ports) == 2


class TestClusterFile:
    def test_roster_lists_every_member(self, rooms_dir: Path, live_room: dict) -> None:
        _join(rooms_dir, "--persona", "reviewer", "--room", "desk")
        _join(rooms_dir, "--persona", "coder", "--room", "desk", "--as", "builder")

        cluster = room_mod._cluster_file_path("desk", rooms_dir).read_text(encoding="utf-8")

        assert "peer_id: reviewer" in cluster
        assert "peer_id: builder" in cluster

    def test_leaving_removes_the_peer_from_the_roster(
        self, rooms_dir: Path, live_room: dict
    ) -> None:
        """Remaining members must converge on the new roster without a restart."""
        _join(rooms_dir, "--persona", "reviewer", "--room", "desk")
        _join(rooms_dir, "--persona", "coder", "--room", "desk", "--as", "builder")

        runner.invoke(
            app, ["leave", "--as", "builder", "--room", "desk", "--rooms-dir", str(rooms_dir)]
        )

        cluster = room_mod._cluster_file_path("desk", rooms_dir).read_text(encoding="utf-8")
        assert "peer_id: reviewer" in cluster
        assert "peer_id: builder" not in cluster

    def test_a_failed_join_leaves_no_ghost_peer(self, rooms_dir: Path, live_room: dict) -> None:
        live_room["registered"] = False

        _join(rooms_dir, "--persona", "reviewer", "--room", "desk")

        cluster = room_mod._cluster_file_path("desk", rooms_dir).read_text(encoding="utf-8")
        assert "peer_id: reviewer" not in cluster


class TestMentionParsing:
    """Operator input parsing — resolves what a human typed, nothing more."""

    PEERS = {"reviewer", "builder", "human:jozef"}

    def test_resolves_a_known_handle(self) -> None:
        assert room_mod._parse_mentions("@reviewer look", self.PEERS) == (["reviewer"], [])

    def test_resolves_several_in_order(self) -> None:
        resolved, _ = room_mod._parse_mentions(
            "@builder build it then @reviewer check it", self.PEERS
        )
        assert resolved == ["builder", "reviewer"]

    def test_duplicates_collapse(self) -> None:
        resolved, _ = room_mod._parse_mentions("@reviewer and @reviewer", self.PEERS)
        assert resolved == ["reviewer"]

    def test_matching_is_case_insensitive(self) -> None:
        assert room_mod._parse_mentions("@Reviewer", self.PEERS)[0] == ["reviewer"]

    def test_humans_answer_to_their_bare_name(self) -> None:
        assert room_mod._parse_mentions("@jozef ping", self.PEERS)[0] == ["human:jozef"]

    def test_unknown_handle_is_reported_not_guessed(self) -> None:
        resolved, unresolved = room_mod._parse_mentions("@reviwer look", self.PEERS)
        assert resolved == []
        assert unresolved == ["reviwer"]

    def test_inline_code_does_not_address(self) -> None:
        assert room_mod._parse_mentions("use `@reviewer` here", self.PEERS) == ([], [])

    def test_fenced_block_does_not_address(self) -> None:
        body = "look:\n```python\n@reviewer\n```\nthoughts?"
        assert room_mod._parse_mentions(body, self.PEERS) == ([], [])

    def test_mention_outside_a_fence_still_resolves(self) -> None:
        body = "```\n@builder\n```\n@reviewer please review it"
        assert room_mod._parse_mentions(body, self.PEERS)[0] == ["reviewer"]


class TestPostMentionRouting:
    @pytest.fixture
    def posted(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict]]:
        calls: list[tuple[str, str, dict]] = []
        monkeypatch.setattr(
            room_mod,
            "_post",
            lambda base, path, payload: (calls.append((base, path, payload)), {})[1],
        )
        monkeypatch.setattr(
            room_mod,
            "_fetch_participants",
            lambda rd: [
                {"peer_id": "reviewer"},
                {"peer_id": "builder"},
                {"peer_id": "human:jozef"},
            ],
        )
        monkeypatch.setattr(room_mod, "_last_speaker", lambda rd, exclude: "")
        return calls

    def _cmd(self, rooms_dir: Path, body: str, *extra: str):
        return runner.invoke(
            room_app,
            [
                "post",
                body,
                "--as",
                "human:jozef",
                "--room",
                "desk",
                "--rooms-dir",
                str(rooms_dir),
                *extra,
            ],
        )

    def test_mention_delivers_to_that_member(
        self, rooms_dir: Path, live_room: dict, posted: list
    ) -> None:
        result = self._cmd(rooms_dir, "@reviewer take a look")

        assert result.exit_code == 0, result.output
        assert posted[0][1] == "/api/room/direct"
        assert posted[0][2]["target_peer_id"] == "reviewer"

    def test_two_mentions_fan_out_with_the_whole_body(
        self, rooms_dir: Path, live_room: dict, posted: list
    ) -> None:
        """Each recipient gets the full message, not a per-mention slice."""
        body = "@builder build it then @reviewer check it"
        result = self._cmd(rooms_dir, body)

        assert result.exit_code == 0, result.output
        assert [p["target_peer_id"] for _, _, p in posted] == ["builder", "reviewer"]
        assert all(p["content"] == body for _, _, p in posted)

    def test_self_mention_does_not_reinvoke_the_author(
        self, rooms_dir: Path, live_room: dict, posted: list
    ) -> None:
        result = self._cmd(rooms_dir, "@jozef noting for myself")

        assert result.exit_code == 0, result.output
        assert posted[0][1] == "/api/room/message"

    def test_to_overrides_mentions(self, rooms_dir: Path, live_room: dict, posted: list) -> None:
        result = self._cmd(rooms_dir, "@reviewer look", "--to", "builder")

        assert result.exit_code == 0, result.output
        assert posted[0][2]["target_peer_id"] == "builder"

    def test_misaddress_warns_and_falls_back(
        self, rooms_dir: Path, live_room: dict, posted: list
    ) -> None:
        result = self._cmd(rooms_dir, "@reviwer look")

        assert result.exit_code == 0, result.output
        assert "no member matches @reviwer" in result.output
        assert posted[0][1] == "/api/room/message"

    def test_dry_run_delivers_nothing(self, rooms_dir: Path, live_room: dict, posted: list) -> None:
        result = self._cmd(rooms_dir, "@reviewer look", "--dry-run")

        assert result.exit_code == 0, result.output
        assert posted == []
        assert "recipients: reviewer" in result.output
