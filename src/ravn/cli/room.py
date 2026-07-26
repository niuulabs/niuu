"""ravn room — create and talk to local collaboration rooms.

A room is a Skuld broker running in room mode, bound to one environment id
that doubles as the room's name.  ``ravn room create`` writes the broker
config, supervises the process, and records enough state for the other
subcommands to find it — so a room needs no Volundr, no Postgres, and no
hand-written YAML.

Lifecycle
---------
  ravn room create NAME   — write config, start the broker, verify it answers
  ravn room ls            — list known rooms and whether they are live
  ravn room show NAME     — print one room's definition and status
  ravn room start NAME    — start a stopped room
  ravn room stop NAME     — graceful shutdown; preserves the definition
  ravn room rm NAME       — stop and delete the room directory

Participation (Skuld broker room API)
-------------------------------------
  ravn room join --participant ID --environment NAME [--role owner]
  ravn room message --participant ID --text "..."
  ravn room participants [--environment NAME]
  ravn room heartbeat --participant ID
  ravn room leave --participant ID
  ravn room close --room ROOM_ID

State files (under ~/.ravn/rooms/NAME/)
---------------------------------------
  room.yaml    — room definition (created by create, read by everything else)
  broker.yaml  — generated Skuld broker config
  state.json   — runtime pid (created by start, removed by stop)
  logs/        — broker log
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml

from ravn.cli.process_supervision import find_free_port, is_alive, port_free, stop_pids

room_app = typer.Typer(
    name="room",
    help="Create, supervise, and join local Ravn collaboration rooms.",
    add_completion=False,
)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# First port tried when allocating a room broker; scans upward from here.
_DEFAULT_BASE_PORT = 7500

# Loopback by default: a room is private to this host until deliberately bound
# wider, matching the flock supervisor's posture.
_DEFAULT_HOST = "127.0.0.1"

# Fallback broker URL for participation subcommands when no room is registered
# locally — preserves the pre-existing `ravn room` default.
_DEFAULT_BROKER_URL = "http://127.0.0.1:9000"

# How long `create`/`start` wait for the broker to answer before reporting
# failure, and how often they poll while waiting.
_STARTUP_TIMEOUT_S = 30.0
_STARTUP_POLL_INTERVAL_S = 0.25

# HTTP timeout for the participation subcommands.
_REQUEST_TIMEOUT_S = 10.0

# Longest error body echoed back to the operator.
_ERROR_BODY_LIMIT = 300


def _rooms_dir_default() -> Path:
    return Path.home() / ".ravn" / "rooms"


# ---------------------------------------------------------------------------
# Room definition  (persisted in room.yaml — the source of truth)
# ---------------------------------------------------------------------------


@dataclass
class RoomDef:
    """Static room definition — created by create, read by every other command."""

    name: str
    environment_id: str
    host: str
    port: int
    created_at: str

    @property
    def broker_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def to_yaml(self) -> str:
        return yaml.safe_dump(asdict(self), sort_keys=False)

    @classmethod
    def from_yaml(cls, text: str) -> RoomDef:
        data = yaml.safe_load(text) or {}
        return cls(
            name=str(data["name"]),
            environment_id=str(data["environment_id"]),
            host=str(data.get("host", _DEFAULT_HOST)),
            port=int(data["port"]),
            created_at=str(data.get("created_at", "")),
        )


def _room_dir(name: str, rooms_dir: Path) -> Path:
    return rooms_dir / name


def _room_def_path(name: str, rooms_dir: Path) -> Path:
    return _room_dir(name, rooms_dir) / "room.yaml"


def _broker_config_path(name: str, rooms_dir: Path) -> Path:
    return _room_dir(name, rooms_dir) / "broker.yaml"


def _state_path(name: str, rooms_dir: Path) -> Path:
    return _room_dir(name, rooms_dir) / "state.json"


def _log_path(name: str, rooms_dir: Path) -> Path:
    return _room_dir(name, rooms_dir) / "logs" / "broker.log"


def _load_room_def(name: str, rooms_dir: Path) -> RoomDef | None:
    path = _room_def_path(name, rooms_dir)
    if not path.is_file():
        return None
    return RoomDef.from_yaml(path.read_text(encoding="utf-8"))


def _require_room_def(name: str, rooms_dir: Path) -> RoomDef:
    room_def = _load_room_def(name, rooms_dir)
    if room_def is None:
        typer.echo(
            f"Unknown room {name!r}. Run 'ravn room ls' to see rooms, "
            f"or 'ravn room create {name}' to make one.",
            err=True,
        )
        raise typer.Exit(1)
    return room_def


def _list_room_names(rooms_dir: Path) -> list[str]:
    if not rooms_dir.is_dir():
        return []
    return sorted(p.name for p in rooms_dir.iterdir() if (p / "room.yaml").is_file())


def _load_pid(name: str, rooms_dir: Path) -> int | None:
    path = _state_path(name, rooms_dir)
    if not path.is_file():
        return None
    try:
        pid = int(json.loads(path.read_text(encoding="utf-8"))["pid"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return pid


def _save_pid(name: str, rooms_dir: Path, pid: int) -> None:
    path = _state_path(name, rooms_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": pid}), encoding="utf-8")
    tmp.replace(path)


def _clear_pid(name: str, rooms_dir: Path) -> None:
    with suppress(FileNotFoundError):
        _state_path(name, rooms_dir).unlink()


def _live_pid(name: str, rooms_dir: Path) -> int | None:
    """Return the recorded pid when it is still running, else None."""
    pid = _load_pid(name, rooms_dir)
    if pid is None or not is_alive(pid):
        return None
    return pid


# ---------------------------------------------------------------------------
# Broker config generation
# ---------------------------------------------------------------------------


def _write_broker_config(room_def: RoomDef, rooms_dir: Path) -> Path:
    """Write the Skuld broker config that puts the broker in room mode.

    ``room.environment_id`` is the room name, which is what the participation
    subcommands and every joining Ravn address.
    """
    room_dir = _room_dir(room_def.name, rooms_dir)
    workspace = room_dir / "workspace"
    persist = room_dir / "persist"
    workspace.mkdir(parents=True, exist_ok=True)
    persist.mkdir(parents=True, exist_ok=True)

    config = {
        "host": room_def.host,
        "port": room_def.port,
        "persistence_mount_path": str(persist),
        "session": {
            "id": room_def.name,
            "workspace_dir": str(workspace),
        },
        "room": {
            "enabled": True,
            "environment_id": room_def.environment_id,
        },
    }
    path = _broker_config_path(room_def.name, rooms_dir)
    path.write_text(
        "# Skuld broker config — room "
        f"{room_def.name}\n"
        "# Generated by: ravn room create\n"
        "# Edit as needed; 'ravn room start' re-reads this file.\n"
        + yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _spawn_broker(room_def: RoomDef, rooms_dir: Path) -> int:
    """Start the room's broker process detached and return its pid."""
    log_path = _log_path(room_def.name, rooms_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    config_path = _broker_config_path(room_def.name, rooms_dir)
    with open(log_path, "a") as log_fd:
        proc = subprocess.Popen(
            [sys.executable, "-m", "skuld"],
            env={**os.environ, "NIUU_CONFIG": str(config_path)},
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return proc.pid


def _broker_responding(room_def: RoomDef) -> bool:
    """Return True when the room API answers on the room's broker URL."""
    import httpx  # noqa: PLC0415

    try:
        response = httpx.get(
            f"{room_def.broker_url}/api/room/participants",
            timeout=_STARTUP_POLL_INTERVAL_S * 4,
        )
    except httpx.HTTPError:
        return False
    return response.status_code < 400


def _wait_for_broker(room_def: RoomDef, pid: int, rooms_dir: Path) -> None:
    """Block until the broker answers, or fail with its log tail.

    Reports the concrete failure rather than leaving a half-started room
    looking healthy.
    """
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if _broker_responding(room_def):
            return
        if not is_alive(pid):
            break
        time.sleep(_STARTUP_POLL_INTERVAL_S)

    _clear_pid(room_def.name, rooms_dir)
    stop_pids([pid])
    log_path = _log_path(room_def.name, rooms_dir)
    typer.echo(f"Room {room_def.name!r} broker did not come up on {room_def.broker_url}.", err=True)
    if log_path.is_file():
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        typer.echo(f"--- {log_path} ---", err=True)
        for line in tail:
            typer.echo(line, err=True)
    raise typer.Exit(1)


def _start_room(room_def: RoomDef, rooms_dir: Path) -> int:
    """Start the room's broker and return its pid once it answers."""
    if not port_free(room_def.port, room_def.host):
        typer.echo(
            f"Port {room_def.port} on {room_def.host} is already in use. "
            f"Stop whatever holds it, or recreate the room with --port.",
            err=True,
        )
        raise typer.Exit(1)

    pid = _spawn_broker(room_def, rooms_dir)
    _save_pid(room_def.name, rooms_dir, pid)
    _wait_for_broker(room_def, pid, rooms_dir)
    return pid


# ---------------------------------------------------------------------------
# Lifecycle commands
# ---------------------------------------------------------------------------


@room_app.command("create")
def room_create(
    name: str = typer.Argument(help="Room name. Doubles as the environment id."),
    host: str = typer.Option(_DEFAULT_HOST, "--host", help="Bind address for the room broker."),
    port: int = typer.Option(
        0, "--port", help="Broker port. 0 allocates the first free port from 7500."
    ),
    rooms_dir: str = typer.Option("", "--rooms-dir", help="Override the rooms state directory."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing definition."),
    start: bool = typer.Option(
        True, "--start/--no-start", help="Start the broker after writing the definition."
    ),
) -> None:
    """Create a room and start its broker.

    \b
    Examples:
      ravn room create desk
      ravn room create desk --port 7500
      ravn room create desk --no-start   # write the definition only
    """
    resolved_dir = Path(rooms_dir) if rooms_dir else _rooms_dir_default()
    existing = _load_room_def(name, resolved_dir)
    if existing is not None and not force:
        typer.echo(
            f"Room {name!r} already exists at {_room_dir(name, resolved_dir)}. "
            "Use --force to overwrite, or edit room.yaml directly.",
            err=True,
        )
        raise typer.Exit(1)

    if existing is not None and _live_pid(name, resolved_dir) is not None:
        typer.echo(
            f"Room {name!r} is running. Run 'ravn room stop {name}' before recreating it.",
            err=True,
        )
        raise typer.Exit(1)

    resolved_port = port if port > 0 else find_free_port(_DEFAULT_BASE_PORT, host)
    room_def = RoomDef(
        name=name,
        environment_id=name,
        host=host,
        port=resolved_port,
        created_at=datetime.now(UTC).isoformat(),
    )

    _room_dir(name, resolved_dir).mkdir(parents=True, exist_ok=True)
    _room_def_path(name, resolved_dir).write_text(room_def.to_yaml(), encoding="utf-8")
    config_path = _write_broker_config(room_def, resolved_dir)

    typer.echo(f"Room {name!r} created at {_room_dir(name, resolved_dir)}")
    typer.echo(f"  Definition:    {_room_def_path(name, resolved_dir)}")
    typer.echo(f"  Broker config: {config_path}")
    typer.echo(f"  Broker URL:    {room_def.broker_url}")

    if not start:
        typer.echo("")
        typer.echo(f"Start it with:  ravn room start {name}")
        return

    pid = _start_room(room_def, resolved_dir)
    typer.echo(f"  Broker pid:    {pid}")
    typer.echo("")
    typer.echo("Join it with:")
    typer.echo(f"  ravn room join --participant human:you --environment {name} --role owner")


@room_app.command("ls")
def room_ls(
    rooms_dir: str = typer.Option("", "--rooms-dir", help="Override the rooms state directory."),
) -> None:
    """List known rooms and whether their brokers are live."""
    resolved_dir = Path(rooms_dir) if rooms_dir else _rooms_dir_default()
    names = _list_room_names(resolved_dir)
    if not names:
        typer.echo(f"No rooms in {resolved_dir}. Create one with 'ravn room create <name>'.")
        return

    typer.echo(f"{'NAME':<24} {'STATUS':<10} {'PID':<8} URL")
    for name in names:
        room_def = _load_room_def(name, resolved_dir)
        if room_def is None:
            continue
        pid = _live_pid(name, resolved_dir)
        status = "running" if pid is not None else "stopped"
        typer.echo(f"{name:<24} {status:<10} {str(pid or '-'):<8} {room_def.broker_url}")


@room_app.command("show")
def room_show(
    name: str = typer.Argument(help="Room name."),
    rooms_dir: str = typer.Option("", "--rooms-dir", help="Override the rooms state directory."),
) -> None:
    """Show one room's definition, status, and file locations."""
    resolved_dir = Path(rooms_dir) if rooms_dir else _rooms_dir_default()
    room_def = _require_room_def(name, resolved_dir)
    pid = _live_pid(name, resolved_dir)

    typer.echo(f"name:           {room_def.name}")
    typer.echo(f"environment_id: {room_def.environment_id}")
    typer.echo(f"broker_url:     {room_def.broker_url}")
    typer.echo(f"created_at:     {room_def.created_at}")
    typer.echo(f"status:         {'running' if pid is not None else 'stopped'}")
    typer.echo(f"pid:            {pid if pid is not None else '-'}")
    typer.echo(f"definition:     {_room_def_path(name, resolved_dir)}")
    typer.echo(f"broker config:  {_broker_config_path(name, resolved_dir)}")
    typer.echo(f"log:            {_log_path(name, resolved_dir)}")


@room_app.command("start")
def room_start(
    name: str = typer.Argument(help="Room name."),
    rooms_dir: str = typer.Option("", "--rooms-dir", help="Override the rooms state directory."),
) -> None:
    """Start a stopped room's broker."""
    resolved_dir = Path(rooms_dir) if rooms_dir else _rooms_dir_default()
    room_def = _require_room_def(name, resolved_dir)

    running = _live_pid(name, resolved_dir)
    if running is not None:
        typer.echo(f"Room {name!r} is already running (pid {running}).")
        return

    pid = _start_room(room_def, resolved_dir)
    typer.echo(f"Room {name!r} started (pid {pid}) at {room_def.broker_url}")


@room_app.command("stop")
def room_stop(
    name: str = typer.Argument(help="Room name."),
    rooms_dir: str = typer.Option("", "--rooms-dir", help="Override the rooms state directory."),
) -> None:
    """Stop a room's broker. Preserves the definition."""
    resolved_dir = Path(rooms_dir) if rooms_dir else _rooms_dir_default()
    _require_room_def(name, resolved_dir)

    pid = _live_pid(name, resolved_dir)
    if pid is None:
        _clear_pid(name, resolved_dir)
        typer.echo(f"Room {name!r} is not running.")
        return

    stop_pids([pid])
    _clear_pid(name, resolved_dir)
    typer.echo(f"Room {name!r} stopped.")


@room_app.command("rm")
def room_rm(
    name: str = typer.Argument(help="Room name."),
    rooms_dir: str = typer.Option("", "--rooms-dir", help="Override the rooms state directory."),
    force: bool = typer.Option(
        False, "--force", "-f", help="Delete without the interactive confirmation."
    ),
) -> None:
    """Stop a room and delete its directory, including its transcript logs."""
    import shutil  # noqa: PLC0415

    resolved_dir = Path(rooms_dir) if rooms_dir else _rooms_dir_default()
    _require_room_def(name, resolved_dir)
    room_dir = _room_dir(name, resolved_dir)

    if not force:
        typer.confirm(f"Delete room {name!r} and everything under {room_dir}?", abort=True)

    pid = _live_pid(name, resolved_dir)
    if pid is not None:
        stop_pids([pid])

    shutil.rmtree(room_dir)
    typer.echo(f"Room {name!r} removed.")


# ---------------------------------------------------------------------------
# Participation commands  (Skuld broker room API)
# ---------------------------------------------------------------------------


def _resolve_broker_url(broker_url: str, environment: str, rooms_dir: str) -> str:
    """Resolve the broker URL for a participation subcommand.

    An explicit ``--broker-url`` always wins.  Otherwise a locally registered
    room whose name matches ``--environment`` supplies its own URL, so
    ``--environment desk`` alone is enough once the room exists.
    """
    if broker_url:
        return broker_url.rstrip("/")

    name = environment.strip()
    if name:
        resolved_dir = Path(rooms_dir) if rooms_dir else _rooms_dir_default()
        room_def = _load_room_def(name, resolved_dir)
        if room_def is not None:
            return room_def.broker_url

    return _DEFAULT_BROKER_URL


def _post(base: str, path: str, payload: dict) -> dict:
    import httpx  # noqa: PLC0415

    response = httpx.post(f"{base}{path}", json=payload, timeout=_REQUEST_TIMEOUT_S)
    if response.status_code >= 400:
        typer.echo(
            f"error {response.status_code}: {response.text[:_ERROR_BODY_LIMIT]}",
            err=True,
        )
        raise typer.Exit(1)
    return response.json()


_BROKER_URL_OPTION = typer.Option(
    "",
    "--broker-url",
    envvar="SKULD_BROKER_URL",
    help="Skuld broker base URL. Defaults to the named room's broker.",
)
_ROOMS_DIR_OPTION = typer.Option("", "--rooms-dir", help="Override the rooms state directory.")


@room_app.command("join")
def room_join(
    participant: str = typer.Option(..., "--participant", help="Participant id, e.g. human:jozef."),
    environment: str = typer.Option(..., "--environment", help="Environment (room) id to join."),
    role: str = typer.Option(
        "observer", "--role", help="Room role: observer|teacher|approver|debugger|owner."
    ),
    room_id: str = typer.Option("", "--room", help="Optional huddle room id."),
    broker_url: str = _BROKER_URL_OPTION,
    rooms_dir: str = _ROOMS_DIR_OPTION,
) -> None:
    """Join a live room as a human participant."""
    base = _resolve_broker_url(broker_url, environment, rooms_dir)
    result = _post(
        base,
        "/api/room/join",
        {
            "participant_id": participant,
            "display_name": participant,
            "environment_id": environment,
            "role": role,
            "room_id": room_id,
        },
    )
    meta = result.get("participant", result)
    typer.echo(
        f"joined {environment} as {participant} ({role}); "
        f"capabilities: {', '.join(meta.get('capabilities', []))}"
    )


@room_app.command("leave")
def room_leave(
    participant: str = typer.Option(..., "--participant", help="Participant id."),
    environment: str = typer.Option("", "--environment", help="Environment (room) id."),
    reason: str = typer.Option("left", "--reason", help="Reason recorded for the departure."),
    broker_url: str = _BROKER_URL_OPTION,
    rooms_dir: str = _ROOMS_DIR_OPTION,
) -> None:
    """Leave a live room."""
    base = _resolve_broker_url(broker_url, environment, rooms_dir)
    _post(base, "/api/room/leave", {"participant_id": participant, "reason": reason})
    typer.echo(f"left: {participant}")


@room_app.command("message")
def room_message(
    participant: str = typer.Option(..., "--participant", help="Participant id."),
    text: str = typer.Option(..., "--text", help="Message text."),
    environment: str = typer.Option("", "--environment", help="Environment (room) id."),
    room_id: str = typer.Option("", "--room", help="Optional huddle room id."),
    broker_url: str = _BROKER_URL_OPTION,
    rooms_dir: str = _ROOMS_DIR_OPTION,
) -> None:
    """Post a message into a live room."""
    base = _resolve_broker_url(broker_url, environment, rooms_dir)
    _post(
        base,
        "/api/room/message",
        {"participant_id": participant, "content": text, "room_id": room_id},
    )
    typer.echo("sent")


@room_app.command("heartbeat")
def room_heartbeat(
    participant: str = typer.Option(..., "--participant", help="Participant id."),
    environment: str = typer.Option("", "--environment", help="Environment (room) id."),
    broker_url: str = _BROKER_URL_OPTION,
    rooms_dir: str = _ROOMS_DIR_OPTION,
) -> None:
    """Refresh a participant's presence so it is not swept as expired."""
    base = _resolve_broker_url(broker_url, environment, rooms_dir)
    _post(base, "/api/room/heartbeat", {"participant_id": participant})
    typer.echo("heartbeat recorded")


@room_app.command("close")
def room_close(
    room_id: str = typer.Option(..., "--room", help="Huddle room id to close."),
    environment: str = typer.Option("", "--environment", help="Environment (room) id."),
    reason: str = typer.Option("closed", "--reason", help="Reason recorded for the closure."),
    broker_url: str = _BROKER_URL_OPTION,
    rooms_dir: str = _ROOMS_DIR_OPTION,
) -> None:
    """Close a huddle, publishing its transcript for archival."""
    base = _resolve_broker_url(broker_url, environment, rooms_dir)
    result = _post(base, "/api/room/close", {"room_id": room_id, "reason": reason})
    typer.echo(f"closed {room_id}; transcript: {result.get('transcriptRef', '-')}")


@room_app.command("participants")
def room_participants(
    environment: str = typer.Option("", "--environment", help="Environment (room) id."),
    broker_url: str = _BROKER_URL_OPTION,
    rooms_dir: str = _ROOMS_DIR_OPTION,
) -> None:
    """List the participants currently in a room."""
    import httpx  # noqa: PLC0415

    base = _resolve_broker_url(broker_url, environment, rooms_dir)
    params = {"environment_id": environment} if environment else None
    response = httpx.get(f"{base}/api/room/participants", params=params, timeout=_REQUEST_TIMEOUT_S)
    response.raise_for_status()
    for entry in response.json().get("participants", []):
        typer.echo(
            f"- {entry.get('peer_id')} [{entry.get('participant_type')}] "
            f"{entry.get('authority_role') or ''} {entry.get('status') or ''}"
        )
