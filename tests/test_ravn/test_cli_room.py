"""Tests for ``ravn room`` — room lifecycle and broker-URL resolution.

The broker process itself is never spawned here: ``_spawn_broker`` and the
readiness probe are the seams, so these tests cover the state machine (define,
start, stop, remove) and the resolution rules without binding ports.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from ravn.cli import room as room_mod
from ravn.cli.room import RoomDef, room_app

runner = CliRunner()


@pytest.fixture
def rooms_dir(tmp_path: Path) -> Path:
    return tmp_path / "rooms"


@pytest.fixture
def fake_broker(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace process spawning and readiness probing with recorded fakes."""
    state: dict = {"spawned": [], "responding": True, "pid": 4242}

    def _spawn(room_def: RoomDef, dirs: Path) -> int:
        state["spawned"].append(room_def.name)
        return int(state["pid"])

    monkeypatch.setattr(room_mod, "_spawn_broker", _spawn)
    monkeypatch.setattr(room_mod, "_broker_responding", lambda room_def: state["responding"])
    monkeypatch.setattr(room_mod, "port_free", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(room_mod, "is_alive", lambda pid: pid == state["pid"])
    monkeypatch.setattr(room_mod, "stop_pids", lambda pids, **kw: None)
    return state


def _create(rooms_dir: Path, name: str = "desk", *extra: str):
    return runner.invoke(room_app, ["create", name, "--rooms-dir", str(rooms_dir), *extra])


class TestRoomCreate:
    def test_create_writes_definition_and_broker_config(
        self, rooms_dir: Path, fake_broker: dict
    ) -> None:
        result = _create(rooms_dir, "desk", "--port", "7501")

        assert result.exit_code == 0, result.output
        room_def = room_mod._load_room_def("desk", rooms_dir)
        assert room_def is not None
        assert room_def.environment_id == "desk"
        assert room_def.port == 7501
        assert room_def.broker_url == "http://127.0.0.1:7501"
        assert fake_broker["spawned"] == ["desk"]

    def test_broker_config_enables_room_mode(self, rooms_dir: Path, fake_broker: dict) -> None:
        import yaml

        _create(rooms_dir, "desk", "--port", "7501")

        config = yaml.safe_load(
            room_mod._broker_config_path("desk", rooms_dir).read_text(encoding="utf-8")
        )
        assert config["room"] == {"enabled": True, "environment_id": "desk"}
        assert config["port"] == 7501
        # The broker refuses to start without a writable workspace, so the
        # generated config must point at the room's own directory.
        assert config["session"]["workspace_dir"].startswith(str(rooms_dir))
        assert config["persistence_mount_path"].startswith(str(rooms_dir))

    def test_no_start_skips_the_broker(self, rooms_dir: Path, fake_broker: dict) -> None:
        result = _create(rooms_dir, "desk", "--no-start")

        assert result.exit_code == 0, result.output
        assert fake_broker["spawned"] == []
        assert room_mod._load_pid("desk", rooms_dir) is None

    def test_duplicate_create_is_refused_without_force(
        self, rooms_dir: Path, fake_broker: dict
    ) -> None:
        _create(rooms_dir, "desk", "--no-start")
        result = _create(rooms_dir, "desk", "--no-start")

        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_force_recreates_a_stopped_room(self, rooms_dir: Path, fake_broker: dict) -> None:
        _create(rooms_dir, "desk", "--no-start", "--port", "7501")
        result = _create(rooms_dir, "desk", "--no-start", "--force", "--port", "7502")

        assert result.exit_code == 0, result.output
        room_def = room_mod._load_room_def("desk", rooms_dir)
        assert room_def is not None
        assert room_def.port == 7502

    def test_force_is_refused_while_the_room_runs(self, rooms_dir: Path, fake_broker: dict) -> None:
        _create(rooms_dir, "desk", "--port", "7501")
        result = _create(rooms_dir, "desk", "--force", "--port", "7502")

        assert result.exit_code == 1
        assert "is running" in result.output

    def test_port_in_use_fails_loudly(
        self, rooms_dir: Path, fake_broker: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(room_mod, "port_free", lambda port, host="127.0.0.1": False)

        result = _create(rooms_dir, "desk", "--port", "7501")

        assert result.exit_code == 1
        assert "already in use" in result.output
        assert fake_broker["spawned"] == []

    def test_unresponsive_broker_reports_failure_and_clears_state(
        self, rooms_dir: Path, fake_broker: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_broker["responding"] = False
        # A dead child short-circuits the wait instead of burning the timeout.
        monkeypatch.setattr(room_mod, "is_alive", lambda pid: False)

        result = _create(rooms_dir, "desk", "--port", "7501")

        assert result.exit_code == 1
        assert "did not come up" in result.output
        assert room_mod._load_pid("desk", rooms_dir) is None


class TestRoomLifecycle:
    def test_ls_reports_running_and_stopped(self, rooms_dir: Path, fake_broker: dict) -> None:
        _create(rooms_dir, "desk", "--port", "7501")
        _create(rooms_dir, "lab", "--no-start", "--port", "7502")

        result = runner.invoke(room_app, ["ls", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 0, result.output
        assert "desk" in result.output
        assert "running" in result.output
        assert "lab" in result.output
        assert "stopped" in result.output

    def test_ls_on_empty_directory_is_not_an_error(self, rooms_dir: Path) -> None:
        result = runner.invoke(room_app, ["ls", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 0
        assert "No rooms" in result.output

    def test_show_reports_status(self, rooms_dir: Path, fake_broker: dict) -> None:
        _create(rooms_dir, "desk", "--port", "7501")

        result = runner.invoke(room_app, ["show", "desk", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 0, result.output
        assert "environment_id: desk" in result.output
        assert "status:         running" in result.output

    def test_show_unknown_room_fails(self, rooms_dir: Path) -> None:
        result = runner.invoke(room_app, ["show", "ghost", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 1
        assert "Unknown room" in result.output

    def test_stop_clears_recorded_pid(self, rooms_dir: Path, fake_broker: dict) -> None:
        _create(rooms_dir, "desk", "--port", "7501")
        assert room_mod._load_pid("desk", rooms_dir) is not None

        result = runner.invoke(room_app, ["stop", "desk", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 0, result.output
        assert room_mod._load_pid("desk", rooms_dir) is None

    def test_stop_when_not_running_is_idempotent(self, rooms_dir: Path, fake_broker: dict) -> None:
        _create(rooms_dir, "desk", "--no-start")

        result = runner.invoke(room_app, ["stop", "desk", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 0
        assert "not running" in result.output

    def test_start_is_a_no_op_when_already_running(
        self, rooms_dir: Path, fake_broker: dict
    ) -> None:
        _create(rooms_dir, "desk", "--port", "7501")
        result = runner.invoke(room_app, ["start", "desk", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 0, result.output
        assert "already running" in result.output
        assert fake_broker["spawned"] == ["desk"]

    def test_start_after_stop_respawns(self, rooms_dir: Path, fake_broker: dict) -> None:
        _create(rooms_dir, "desk", "--no-start", "--port", "7501")

        result = runner.invoke(room_app, ["start", "desk", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 0, result.output
        assert fake_broker["spawned"] == ["desk"]

    def test_rm_deletes_the_room_directory(self, rooms_dir: Path, fake_broker: dict) -> None:
        _create(rooms_dir, "desk", "--port", "7501")

        result = runner.invoke(room_app, ["rm", "desk", "--force", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 0, result.output
        assert not room_mod._room_dir("desk", rooms_dir).exists()

    def test_rm_unknown_room_fails(self, rooms_dir: Path) -> None:
        result = runner.invoke(room_app, ["rm", "ghost", "--force", "--rooms-dir", str(rooms_dir)])

        assert result.exit_code == 1


class TestPidState:
    def test_corrupt_state_file_reads_as_no_pid(self, rooms_dir: Path) -> None:
        room_dir = room_mod._room_dir("desk", rooms_dir)
        room_dir.mkdir(parents=True)
        room_mod._state_path("desk", rooms_dir).write_text("not json", encoding="utf-8")

        assert room_mod._load_pid("desk", rooms_dir) is None

    def test_state_file_without_pid_key_reads_as_no_pid(self, rooms_dir: Path) -> None:
        room_dir = room_mod._room_dir("desk", rooms_dir)
        room_dir.mkdir(parents=True)
        room_mod._state_path("desk", rooms_dir).write_text(json.dumps({}), encoding="utf-8")

        assert room_mod._load_pid("desk", rooms_dir) is None


class TestBrokerUrlResolution:
    def test_explicit_broker_url_wins(self, rooms_dir: Path, fake_broker: dict) -> None:
        _create(rooms_dir, "desk", "--no-start", "--port", "7501")

        resolved = room_mod._resolve_broker_url("http://elsewhere:9999/", "desk", str(rooms_dir))

        assert resolved == "http://elsewhere:9999"

    def test_environment_name_resolves_to_the_registered_room(
        self, rooms_dir: Path, fake_broker: dict
    ) -> None:
        _create(rooms_dir, "desk", "--no-start", "--port", "7501")

        resolved = room_mod._resolve_broker_url("", "desk", str(rooms_dir))

        assert resolved == "http://127.0.0.1:7501"

    def test_unknown_environment_falls_back_to_the_default_broker(self, rooms_dir: Path) -> None:
        resolved = room_mod._resolve_broker_url("", "ghost", str(rooms_dir))

        assert resolved == room_mod._DEFAULT_BROKER_URL

    def test_no_environment_falls_back_to_the_default_broker(self, rooms_dir: Path) -> None:
        resolved = room_mod._resolve_broker_url("", "", str(rooms_dir))

        assert resolved == room_mod._DEFAULT_BROKER_URL


class TestRoomDefSerialisation:
    def test_round_trips_through_yaml(self) -> None:
        original = RoomDef(
            name="desk",
            environment_id="desk",
            host="127.0.0.1",
            port=7501,
            created_at="2026-07-26T00:00:00+00:00",
        )

        restored = RoomDef.from_yaml(original.to_yaml())

        assert restored == original

    def test_host_defaults_when_absent(self) -> None:
        restored = RoomDef.from_yaml("name: desk\nenvironment_id: desk\nport: 7501\n")

        assert restored.host == room_mod._DEFAULT_HOST
        assert restored.created_at == ""


class TestParticipationCommands:
    """The participation subcommands are thin wrappers over the broker API."""

    def test_join_posts_the_expected_payload(
        self, rooms_dir: Path, fake_broker: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _create(rooms_dir, "desk", "--no-start", "--port", "7501")
        calls: list[tuple[str, str, dict]] = []

        def _fake_post(base: str, path: str, payload: dict) -> dict:
            calls.append((base, path, payload))
            return {"participant": {"capabilities": ["view", "reply"]}}

        monkeypatch.setattr(room_mod, "_post", _fake_post)

        result = runner.invoke(
            room_app,
            [
                "join",
                "--participant",
                "human:jozef",
                "--environment",
                "desk",
                "--role",
                "owner",
                "--rooms-dir",
                str(rooms_dir),
            ],
        )

        assert result.exit_code == 0, result.output
        base, path, payload = calls[0]
        assert base == "http://127.0.0.1:7501"
        assert path == "/api/room/join"
        assert payload["participant_id"] == "human:jozef"
        assert payload["environment_id"] == "desk"
        assert payload["role"] == "owner"

    def test_message_requires_text(self, rooms_dir: Path) -> None:
        result = runner.invoke(
            room_app,
            ["message", "--participant", "human:jozef", "--rooms-dir", str(rooms_dir)],
        )

        assert result.exit_code != 0

    def test_message_posts_content(
        self, rooms_dir: Path, fake_broker: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _create(rooms_dir, "desk", "--no-start", "--port", "7501")
        calls: list[tuple[str, str, dict]] = []
        monkeypatch.setattr(
            room_mod,
            "_post",
            lambda base, path, payload: (calls.append((base, path, payload)), {})[1],
        )

        result = runner.invoke(
            room_app,
            [
                "message",
                "--participant",
                "human:jozef",
                "--environment",
                "desk",
                "--text",
                "hello room",
                "--rooms-dir",
                str(rooms_dir),
            ],
        )

        assert result.exit_code == 0, result.output
        _, path, payload = calls[0]
        assert path == "/api/room/message"
        assert payload["content"] == "hello room"


class TestStartupFailureReporting:
    def test_failure_echoes_the_broker_log_tail(
        self, rooms_dir: Path, fake_broker: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A room that won't start must show why, not just that it didn't."""
        fake_broker["responding"] = False
        monkeypatch.setattr(room_mod, "is_alive", lambda pid: False)

        def _spawn_and_log(room_def: RoomDef, dirs: Path) -> int:
            log = room_mod._log_path(room_def.name, dirs)
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("OSError: Read-only file system: '/volundr'\n", encoding="utf-8")
            return int(fake_broker["pid"])

        monkeypatch.setattr(room_mod, "_spawn_broker", _spawn_and_log)

        result = _create(rooms_dir, "desk", "--port", "7501")

        assert result.exit_code == 1
        assert "Read-only file system" in result.output

    def test_rm_requires_confirmation_without_force(
        self, rooms_dir: Path, fake_broker: dict
    ) -> None:
        _create(rooms_dir, "desk", "--no-start")

        result = runner.invoke(room_app, ["rm", "desk", "--rooms-dir", str(rooms_dir)], input="n\n")

        assert result.exit_code != 0
        assert room_mod._room_dir("desk", rooms_dir).exists()


class TestRemainingParticipationCommands:
    @pytest.fixture
    def posted(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict]]:
        calls: list[tuple[str, str, dict]] = []

        def _fake_post(base: str, path: str, payload: dict) -> dict:
            calls.append((base, path, payload))
            return {"transcriptRef": "mimir://t/1"}

        monkeypatch.setattr(room_mod, "_post", _fake_post)
        return calls

    def test_leave(self, rooms_dir: Path, posted: list) -> None:
        result = runner.invoke(
            room_app,
            ["leave", "--participant", "human:jozef", "--rooms-dir", str(rooms_dir)],
        )

        assert result.exit_code == 0, result.output
        assert posted[0][1] == "/api/room/leave"
        assert posted[0][2]["reason"] == "left"

    def test_heartbeat(self, rooms_dir: Path, posted: list) -> None:
        result = runner.invoke(
            room_app,
            ["heartbeat", "--participant", "human:jozef", "--rooms-dir", str(rooms_dir)],
        )

        assert result.exit_code == 0, result.output
        assert posted[0][1] == "/api/room/heartbeat"

    def test_close_reports_the_transcript(self, rooms_dir: Path, posted: list) -> None:
        result = runner.invoke(
            room_app, ["close", "--room", "huddle-1", "--rooms-dir", str(rooms_dir)]
        )

        assert result.exit_code == 0, result.output
        assert posted[0][1] == "/api/room/close"
        assert "mimir://t/1" in result.output

    def test_participants_lists_each_peer(
        self, rooms_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Response:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "participants": [
                        {
                            "peer_id": "human:jozef",
                            "participant_type": "human",
                            "authority_role": "owner",
                            "status": "idle",
                        },
                        {
                            "peer_id": "ravn-reviewer",
                            "participant_type": "ravn",
                            "authority_role": "",
                            "status": "running",
                        },
                    ]
                }

        monkeypatch.setattr(
            room_mod.httpx if hasattr(room_mod, "httpx") else __import__("httpx"),
            "get",
            lambda *a, **kw: _Response(),
        )

        result = runner.invoke(
            room_app, ["participants", "--environment", "desk", "--rooms-dir", str(rooms_dir)]
        )

        assert result.exit_code == 0, result.output
        assert "human:jozef" in result.output
        assert "ravn-reviewer" in result.output


class TestPostErrorHandling:
    def test_http_error_status_exits_with_the_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A broker rejection must surface, not be swallowed into a success message."""
        import httpx

        class _Response:
            status_code = 403
            text = "participant lacks capability"

        monkeypatch.setattr(httpx, "post", lambda *a, **kw: _Response())

        with pytest.raises(typer.Exit) as exc:
            room_mod._post("http://127.0.0.1:7500", "/api/room/message", {})

        assert exc.value.exit_code == 1

    def test_success_returns_the_decoded_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        class _Response:
            status_code = 200

            def json(self) -> dict:
                return {"status": "ok"}

        monkeypatch.setattr(httpx, "post", lambda *a, **kw: _Response())

        assert room_mod._post("http://x", "/p", {}) == {"status": "ok"}


class TestBrokerProbe:
    def test_connection_error_reads_as_not_responding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        def _raise(*args, **kwargs):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "get", _raise)
        room_def = RoomDef("desk", "desk", "127.0.0.1", 7501, "")

        assert room_mod._broker_responding(room_def) is False

    def test_ok_status_reads_as_responding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        class _Response:
            status_code = 200

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Response())
        room_def = RoomDef("desk", "desk", "127.0.0.1", 7501, "")

        assert room_mod._broker_responding(room_def) is True

    def test_error_status_reads_as_not_responding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        class _Response:
            status_code = 500

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Response())
        room_def = RoomDef("desk", "desk", "127.0.0.1", 7501, "")

        assert room_mod._broker_responding(room_def) is False


class TestSpawnBroker:
    def test_spawns_the_broker_with_its_own_config(
        self, rooms_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The child must be pointed at the room's generated config, detached."""
        recorded: dict = {}

        class _Proc:
            pid = 5150

        def _fake_popen(cmd, **kwargs):
            recorded["cmd"] = cmd
            recorded["env"] = kwargs["env"]
            recorded["start_new_session"] = kwargs["start_new_session"]
            return _Proc()

        monkeypatch.setattr(room_mod.subprocess, "Popen", _fake_popen)

        room_def = RoomDef("desk", "desk", "127.0.0.1", 7501, "")
        room_mod._room_dir("desk", rooms_dir).mkdir(parents=True)
        room_mod._write_broker_config(room_def, rooms_dir)

        pid = room_mod._spawn_broker(room_def, rooms_dir)

        assert pid == 5150
        assert recorded["cmd"][1:] == ["-m", "skuld"]
        assert recorded["env"]["NIUU_CONFIG"] == str(
            room_mod._broker_config_path("desk", rooms_dir)
        )
        assert recorded["start_new_session"] is True


class TestDefaultRoomsDir:
    def test_defaults_under_the_ravn_home(self) -> None:
        assert room_mod._rooms_dir_default() == Path.home() / ".ravn" / "rooms"


def test_room_dir_layout_is_contained(tmp_path: Path, fake_broker: dict) -> None:
    """Everything a room owns lives under its own directory, so rm is total."""
    rooms = tmp_path / "rooms"
    _create(rooms, "desk", "--port", "7501")

    room_dir = rooms / "desk"
    for path in (
        room_mod._room_def_path("desk", rooms),
        room_mod._broker_config_path("desk", rooms),
        room_mod._state_path("desk", rooms),
        room_mod._log_path("desk", rooms).parent,
    ):
        assert os.path.commonpath([str(room_dir), str(path)]) == str(room_dir)
