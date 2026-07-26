"""End-to-end CLI coverage for `ravn flock` commands (NIU-1054 follow-up).

These exercise the command layer of ``ravn.cli.flock`` through Typer's
``CliRunner`` against a temp ``--flock-dir``. Process spawning, port probing,
and sleeps are monkeypatched so nothing real is launched.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ravn.cli import flock as flock_mod
from ravn.cli.flock import (
    FlockRuntime,
    _find_tail,
    _load_flock_def,
    _load_runtime,
    _python_tail,
    _save_runtime,
    flock_app,
)

runner = CliRunner()

# A pid that is essentially never alive — os.kill(pid, 0) raises ProcessLookupError.
_DEAD_PID = 2**30


def _init(tmp_path: Path, *args: str) -> None:
    """Initialise a single-node flock and assert it succeeded."""
    result = runner.invoke(
        flock_app,
        ["init", "coordinator", "--flock-dir", str(tmp_path), *args],
    )
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_writes_definition_and_node_configs(tmp_path: Path) -> None:
    result = runner.invoke(
        flock_app,
        ["init", "coordinator", "--flock-dir", str(tmp_path), "--discovery", "static"],
    )

    assert result.exit_code == 0, result.output
    assert "Flock initialised" in result.output
    flock_def = _load_flock_def(tmp_path)
    assert flock_def is not None
    assert [n.persona for n in flock_def.nodes] == ["coordinator"]
    # static discovery emits a cluster.yaml alongside the node config.
    assert (tmp_path / "cluster.yaml").exists()
    assert (tmp_path / "node-coordinator.yaml").exists()
    assert (tmp_path / "logs").is_dir()


def test_init_defaults_to_the_three_built_in_personas(tmp_path: Path) -> None:
    result = runner.invoke(flock_app, ["init", "--flock-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    flock_def = _load_flock_def(tmp_path)
    assert [n.persona for n in flock_def.nodes] == [
        "coordinator",
        "coding-agent",
        "research-agent",
    ]


def test_init_without_http_gateway_disables_node_gateway(tmp_path: Path) -> None:
    _init(tmp_path, "--no-http-gateway")

    flock_def = _load_flock_def(tmp_path)
    assert flock_def.http_gateway_enabled is False
    config = (tmp_path / "node-coordinator.yaml").read_text()
    assert "enabled: false" in config


def test_init_rejects_unknown_mesh_transport(tmp_path: Path) -> None:
    result = runner.invoke(
        flock_app,
        ["init", "coordinator", "--flock-dir", str(tmp_path), "--mesh-transport", "carrier-pigeon"],
    )
    assert result.exit_code == 1
    assert "Unsupported mesh transport" in result.output


def test_init_ipc_requires_static_discovery(tmp_path: Path) -> None:
    result = runner.invoke(
        flock_app,
        [
            "init",
            "coordinator",
            "--flock-dir",
            str(tmp_path),
            "--mesh-transport",
            "ipc",
            "--discovery",
            "mdns",
        ],
    )
    assert result.exit_code == 1
    assert "IPC mesh transport requires --discovery static" in result.output


def test_init_rejects_unknown_persona(tmp_path: Path) -> None:
    result = runner.invoke(
        flock_app,
        ["init", "not-a-real-persona", "--flock-dir", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "Unknown persona" in result.output


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    _init(tmp_path)
    result = runner.invoke(flock_app, ["init", "coordinator", "--flock-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "already initialised" in result.output


def test_init_force_overwrites_existing_definition(tmp_path: Path) -> None:
    _init(tmp_path)
    result = runner.invoke(
        flock_app,
        ["init", "coder", "--flock-dir", str(tmp_path), "--force"],
    )
    assert result.exit_code == 0, result.output
    assert [n.persona for n in _load_flock_def(tmp_path).nodes] == ["coder"]


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


def _patch_spawn(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Replace real process spawning, staggers, and port probes."""
    spawned: dict[str, int] = {}

    def _fake_spawn(node, _flock_dir) -> int:
        spawned[node.persona] = 4242 + node.index
        return spawned[node.persona]

    monkeypatch.setattr(flock_mod, "_spawn_node", _fake_spawn)
    monkeypatch.setattr(flock_mod, "_check_ports", lambda _def: [])
    monkeypatch.setattr(flock_mod.time, "sleep", lambda *_a, **_k: None)
    return spawned


def test_start_requires_an_existing_definition(tmp_path: Path) -> None:
    result = runner.invoke(flock_app, ["start", "--flock-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "No flock definition found" in result.output


def test_start_spawns_nodes_and_records_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, "--discovery", "mdns", "--mesh-transport", "tcp")
    spawned = _patch_spawn(monkeypatch)

    result = runner.invoke(flock_app, ["start", "--flock-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Starting flock" in result.output
    assert "mDNS" in result.output  # mdns discovery message
    assert "HTTP / WebSocket endpoints" in result.output  # gateway enabled
    runtime = _load_runtime(tmp_path)
    assert runtime is not None
    assert runtime.pids == spawned


def test_start_ipc_static_without_gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path, "--discovery", "static", "--mesh-transport", "ipc", "--no-http-gateway")
    _patch_spawn(monkeypatch)

    result = runner.invoke(flock_app, ["start", "--flock-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "static cluster.yaml" in result.output
    assert "http=disabled" in result.output
    assert "HTTP / WebSocket endpoints" not in result.output


def test_start_refuses_when_a_flock_is_already_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path)
    # Our own pid is unmistakably alive.
    _save_runtime(FlockRuntime(started_at="now", pids={"coordinator": os.getpid()}), tmp_path)

    result = runner.invoke(flock_app, ["start", "--flock-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "already running" in result.output


def test_start_reports_taken_ports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path)
    monkeypatch.setattr(flock_mod, "_check_ports", lambda _def: [7480, 7481])

    result = runner.invoke(flock_app, ["start", "--flock-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "Ports already in use" in result.output


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_without_runtime_is_a_noop(tmp_path: Path) -> None:
    result = runner.invoke(flock_app, ["stop", "--flock-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "No running flock" in result.output


def test_stop_terminates_pids_and_clears_state(tmp_path: Path) -> None:
    _init(tmp_path)
    _save_runtime(FlockRuntime(started_at="now", pids={"coordinator": _DEAD_PID}), tmp_path)

    result = runner.invoke(flock_app, ["stop", "--flock-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Flock stopped" in result.output
    assert _load_runtime(tmp_path) is None


def test_stop_cleans_up_ipc_sockets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path, "--discovery", "static", "--mesh-transport", "ipc")
    _save_runtime(FlockRuntime(started_at="now", pids={"coordinator": _DEAD_PID}), tmp_path)
    cleaned: list[object] = []
    monkeypatch.setattr(flock_mod, "cleanup_ravn_mesh_sockets", lambda *a, **k: cleaned.append(a))

    result = runner.invoke(flock_app, ["stop", "--flock-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert cleaned  # ipc transport triggers socket cleanup


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_without_definition(tmp_path: Path) -> None:
    result = runner.invoke(flock_app, ["status", "--flock-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "No flock definition found" in result.output


def test_status_reports_stopped_when_no_runtime(tmp_path: Path) -> None:
    _init(tmp_path)
    result = runner.invoke(flock_app, ["status", "--flock-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "stopped" in result.output


def test_status_reports_running_and_dead_pids(tmp_path: Path) -> None:
    _init(tmp_path, "--no-http-gateway")
    _save_runtime(FlockRuntime(started_at="now", pids={"coordinator": os.getpid()}), tmp_path)
    running = runner.invoke(flock_app, ["status", "--flock-dir", str(tmp_path)])
    assert "running" in running.output
    assert "http=disabled" in running.output

    _save_runtime(FlockRuntime(started_at="now", pids={"coordinator": _DEAD_PID}), tmp_path)
    dead = runner.invoke(flock_app, ["status", "--flock-dir", str(tmp_path)])
    assert "DEAD" in dead.output


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_shows_available_personas() -> None:
    result = runner.invoke(flock_app, ["list"])
    assert result.exit_code == 0, result.output
    assert "Available personas" in result.output
    assert "coordinator" in result.output
    assert "[built-in]" in result.output


# ---------------------------------------------------------------------------
# peers
# ---------------------------------------------------------------------------


def test_peers_without_definition(tmp_path: Path) -> None:
    result = runner.invoke(flock_app, ["peers", "--flock-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "No flock definition found" in result.output


def test_peers_without_runtime(tmp_path: Path) -> None:
    _init(tmp_path)
    result = runner.invoke(flock_app, ["peers", "--flock-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "No running flock found" in result.output


def test_peers_with_no_live_nodes(tmp_path: Path) -> None:
    _init(tmp_path)
    _save_runtime(FlockRuntime(started_at="now", pids={"coordinator": _DEAD_PID}), tmp_path)
    result = runner.invoke(flock_app, ["peers", "--flock-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "No live flock nodes found" in result.output


def test_peers_queries_a_live_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAVN_CONFIG", raising=False)
    _init(tmp_path)
    _save_runtime(FlockRuntime(started_at="now", pids={"coordinator": os.getpid()}), tmp_path)

    calls: list[bool] = []

    async def _fake_run_peers(_settings, *, verbose, force_scan) -> None:
        calls.append(verbose)

    import ravn.cli.commands as commands_mod
    import ravn.config as config_mod

    monkeypatch.setattr(commands_mod, "_run_peers", _fake_run_peers)
    monkeypatch.setattr(config_mod, "Settings", object)

    result = runner.invoke(flock_app, ["peers", "--flock-dir", str(tmp_path), "--verbose"])

    assert result.exit_code == 0, result.output
    assert calls == [True]
    # The node's config path is exported for the peers subprocess.
    assert os.environ["RAVN_CONFIG"].endswith("node-coordinator.yaml")


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


def test_logs_without_definition(tmp_path: Path) -> None:
    result = runner.invoke(flock_app, ["logs", "--flock-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "No flock definition found" in result.output


def test_logs_with_no_matching_node(tmp_path: Path) -> None:
    _init(tmp_path)
    result = runner.invoke(flock_app, ["logs", "--flock-dir", str(tmp_path), "--node", "ghost"])
    assert result.exit_code == 1
    assert "No node matching" in result.output


def test_logs_when_no_log_files_written_yet(tmp_path: Path) -> None:
    _init(tmp_path)
    result = runner.invoke(flock_app, ["logs", "--flock-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "No log files found yet" in result.output


def test_logs_uses_tail_binary_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path)
    flock_def = _load_flock_def(tmp_path)
    Path(flock_def.nodes[0].log_path).write_text("hello from coordinator\n")

    ran: list[list[str]] = []
    monkeypatch.setattr(flock_mod, "_find_tail", lambda: "/usr/bin/tail")
    monkeypatch.setattr(flock_mod.subprocess, "run", lambda cmd: ran.append(cmd))

    result = runner.invoke(
        flock_app, ["logs", "--flock-dir", str(tmp_path), "--node", "0", "--no-follow"]
    )

    assert result.exit_code == 0, result.output
    assert ran and ran[0][0] == "/usr/bin/tail"


def test_logs_falls_back_to_python_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path)
    flock_def = _load_flock_def(tmp_path)
    Path(flock_def.nodes[0].log_path).write_text("line-1\nline-2\n")

    monkeypatch.setattr(flock_mod, "_find_tail", lambda: None)

    result = runner.invoke(
        flock_app,
        ["logs", "--flock-dir", str(tmp_path), "--node", "coordinator", "--no-follow"],
    )

    assert result.exit_code == 0, result.output
    assert "line-2" in result.output


# ---------------------------------------------------------------------------
# log tail helpers
# ---------------------------------------------------------------------------


def test_find_tail_returns_a_real_binary_or_none() -> None:
    found = _find_tail()
    assert found is None or Path(found).exists()


def test_python_tail_prints_last_lines_without_following(tmp_path: Path) -> None:
    log = tmp_path / "node.log"
    log.write_text("\n".join(f"line-{i}" for i in range(10)) + "\n")

    # follow=False prints the last *lines* and returns; a missing path is skipped.
    _python_tail([str(log), str(tmp_path / "missing.log")], lines=3, follow=False)


class TestFlockRoomMembership:
    """`flock init --room` makes a flock and a room roster the same ravens."""

    def _room(self, tmp_path: Path) -> Path:
        from ravn.cli.room import RoomDef, _room_def_path

        rooms = tmp_path / "rooms"
        (rooms / "desk").mkdir(parents=True)
        room_def = RoomDef(
            name="desk",
            environment_id="desk",
            host="127.0.0.1",
            port=7500,
            created_at="2026-07-26T00:00:00+00:00",
        )
        _room_def_path("desk", rooms).write_text(room_def.to_yaml(), encoding="utf-8")
        return rooms

    def test_nodes_get_the_room_channel(self, tmp_path: Path) -> None:
        rooms = self._room(tmp_path)

        result = runner.invoke(
            flock_app,
            [
                "init",
                "coordinator",
                "--flock-dir",
                str(tmp_path / "flock"),
                "--room",
                "desk",
                "--rooms-dir",
                str(rooms),
            ],
        )

        assert result.exit_code == 0, result.output
        config = (tmp_path / "flock" / "node-coordinator.yaml").read_text()
        assert "skuld:" in config
        assert "broker_url: ws://127.0.0.1:7500/ws/ravn" in config

    def test_without_room_no_skuld_block_is_added(self, tmp_path: Path) -> None:
        result = runner.invoke(
            flock_app, ["init", "coordinator", "--flock-dir", str(tmp_path / "flock")]
        )

        assert result.exit_code == 0, result.output
        assert "skuld:" not in (tmp_path / "flock" / "node-coordinator.yaml").read_text()

    def test_unknown_room_fails_before_writing_configs(self, tmp_path: Path) -> None:
        """A typo must not produce a flock that quietly joins nothing."""
        rooms = self._room(tmp_path)

        result = runner.invoke(
            flock_app,
            [
                "init",
                "coordinator",
                "--flock-dir",
                str(tmp_path / "flock"),
                "--room",
                "ghost",
                "--rooms-dir",
                str(rooms),
            ],
        )

        assert result.exit_code == 1
        assert "Unknown room 'ghost'" in result.output
        assert not (tmp_path / "flock" / "node-coordinator.yaml").exists()

    def test_room_nodes_are_responsive_by_default(self, tmp_path: Path) -> None:
        """A room node answers what is addressed to it instead of self-driving."""
        rooms = self._room(tmp_path)

        runner.invoke(
            flock_app,
            [
                "init",
                "coordinator",
                "--flock-dir",
                str(tmp_path / "flock"),
                "--room",
                "desk",
                "--rooms-dir",
                str(rooms),
            ],
        )

        config = (tmp_path / "flock" / "node-coordinator.yaml").read_text()
        assert "dream_cycle" in config
        assert "resident_wakefulness" in config

    def test_autonomous_room_nodes_keep_their_triggers(self, tmp_path: Path) -> None:
        rooms = self._room(tmp_path)

        runner.invoke(
            flock_app,
            [
                "init",
                "coordinator",
                "--flock-dir",
                str(tmp_path / "flock"),
                "--room",
                "desk",
                "--rooms-dir",
                str(rooms),
                "--autonomous",
            ],
        )

        config = (tmp_path / "flock" / "node-coordinator.yaml").read_text()
        assert "dream_cycle" not in config

    def test_a_roomless_flock_is_unchanged(self, tmp_path: Path) -> None:
        """Plain `flock init` keeps its long-standing autonomous behaviour."""
        runner.invoke(flock_app, ["init", "coordinator", "--flock-dir", str(tmp_path / "flock")])

        config = (tmp_path / "flock" / "node-coordinator.yaml").read_text()
        assert "dream_cycle" not in config
