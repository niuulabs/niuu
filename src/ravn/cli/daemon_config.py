"""Daemon config generation shared by ``ravn flock`` and ``ravn room``.

Both supervisors have to answer the same question — what YAML makes a
``ravn daemon`` join a room — so the Skuld block is rendered in exactly one
place.  A room member's config is built here too, layered over an optional
base config so an operator's model/provider settings carry through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# WebSocket path the broker exposes for Ravn participants; the peer id is
# appended by the daemon at connect time.
RAVN_WS_PATH = "/ws/ravn"


def room_broker_ws_url(host: str, port: int) -> str:
    """Return the Ravn-participant WebSocket base URL for a room's broker."""
    return f"ws://{host}:{port}{RAVN_WS_PATH}"


def skuld_block(broker_url: str, *, display_name: str = "") -> dict[str, Any]:
    """Return the ``skuld:`` config section that makes a daemon join a room.

    This is the single definition of room membership for a Ravn daemon: the
    drive loop builds its SkuldChannel from it and connects to
    ``<broker_url>/<peer_id>``.
    """
    block: dict[str, Any] = {"enabled": True, "broker_url": broker_url}
    if display_name:
        block["display_name"] = display_name
    return block


def render_skuld_yaml(broker_url: str, *, display_name: str = "") -> str:
    """Render :func:`skuld_block` as a top-level ``skuld:`` YAML section."""
    return yaml.safe_dump({"skuld": skuld_block(broker_url, display_name=display_name)})


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge *overlay* into *base*, recursing into nested mappings."""
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
            continue
        merged[key] = value
    return merged


def load_base_config(path: Path | None) -> dict[str, Any]:
    """Load an operator-supplied base config, or return an empty mapping.

    Raises ``ValueError`` when the file exists but is not a YAML mapping —
    a malformed base config must not silently degrade into defaults.
    """
    if path is None:
        return {}
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Base config {path} is not a YAML mapping.")
    return data


# Trigger sources that make a daemon generate its own work. A room member is
# responsive by default — it answers what is addressed to it — so these are
# switched off unless the operator asks for an autonomous member.
_SELF_DRIVING_SECTIONS = (
    ("mimir", "source_trigger"),
    ("mimir", "staleness_trigger"),
    ("resident_wakefulness", None),
    ("recap", None),
    ("dream_cycle", None),
    ("thread", None),
    ("resident_inbox", None),
)


def quiet_overlay_yaml() -> str:
    """Render :func:`_quiet_overlay` as top-level YAML sections."""
    return yaml.safe_dump(_quiet_overlay(), sort_keys=False)


def _quiet_overlay() -> dict[str, Any]:
    """Return the config sections that disable autonomous work generation."""
    overlay: dict[str, Any] = {}
    for section, subsection in _SELF_DRIVING_SECTIONS:
        if subsection is None:
            overlay[section] = {"enabled": False}
            continue
        overlay.setdefault(section, {})[subsection] = {"enabled": False}
    return overlay


def mesh_block(handle: str, *, pub_port: int, rep_port: int, cluster_file: Path) -> dict[str, Any]:
    """Return the ``mesh:``/``discovery:`` sections that make a member a peer.

    Room membership is a surface; peer-to-peer work still travels over the
    mesh, so a member is wired the same way a flock node is.  Without this a
    member can be seen in a room but cannot be reached by ``route_work`` or
    the cascade delegation tools.

    Discovery is static: a room already knows its own roster, so peers are
    read from a generated cluster file rather than rediscovered over mDNS.
    """
    return {
        "mesh": {
            "enabled": True,
            "adapter": "nng",
            "own_peer_id": handle,
            "nng": {
                "pub_sub_address": f"tcp://0.0.0.0:{pub_port}",
                "req_rep_address": f"tcp://0.0.0.0:{rep_port}",
            },
        },
        "discovery": {
            "enabled": True,
            "adapters": [
                {
                    "adapter": "ravn.adapters.discovery.static.StaticDiscoveryAdapter",
                    "cluster_file": str(cluster_file),
                    "poll_interval_s": 5,
                }
            ],
        },
        "cascade": {"enabled": True},
    }


def render_cluster_yaml(members: list[dict[str, Any]]) -> str:
    """Render the static peer table shared by a room's members."""
    lines = ["# Static peer definitions (auto-generated by ravn join)", "peers:"]
    for member in members:
        lines.extend(
            [
                f"  - peer_id: {member['handle']}",
                f"    persona: {member['persona']}",
                f"    display_name: {member['handle']}",
                f'    pub_address: "tcp://127.0.0.1:{member["pub_port"]}"',
                f'    rep_address: "tcp://127.0.0.1:{member["rep_port"]}"',
            ]
        )
    return "\n".join(lines) + "\n"


def build_member_config(
    *,
    handle: str,
    broker_url: str,
    display_name: str = "",
    memory_db_path: Path | None = None,
    queue_journal_path: Path | None = None,
    autonomous: bool = False,
    mesh_ports: tuple[int, int] | None = None,
    cluster_file: Path | None = None,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the daemon config for one room member.

    The member's *handle* becomes its mesh peer id, which is the identity the
    broker registers and every other participant addresses.  Room membership
    and identity are applied last so a base config cannot accidentally
    override them.

    Unless *autonomous* is set, the self-driving trigger sources are disabled:
    a member joins to take part in the room, not to start generating its own
    work the moment it connects.
    """
    config = dict(base or {})

    # A detached member's log is its only window, so INFO is the useful floor —
    # mesh, discovery and tool wiring all report there. Applied as a default so
    # a base config can still ask for DEBUG.
    config.setdefault("logging", {}).setdefault("level", "INFO")

    overlay: dict[str, Any] = {
        "skuld": skuld_block(broker_url, display_name=display_name or handle),
        "mesh": {"own_peer_id": handle},
        **(
            mesh_block(
                handle,
                pub_port=mesh_ports[0],
                rep_port=mesh_ports[1],
                cluster_file=cluster_file,
            )
            if mesh_ports and cluster_file
            else {}
        ),
        # The drive loop owns the room channel's connection, so a member with
        # initiative disabled would start, find nothing to do, and exit
        # without ever registering.
        "initiative": {"enabled": True},
    }
    if not autonomous:
        overlay = _deep_merge(_quiet_overlay(), overlay)
    if memory_db_path is not None:
        overlay["memory"] = {"backend": "sqlite", "sqlite": {"path": str(memory_db_path)}}
    if queue_journal_path is not None:
        overlay["initiative"]["queue_journal_path"] = str(queue_journal_path)

    return _deep_merge(config, overlay)


def render_member_config(
    *,
    handle: str,
    room_name: str,
    persona: str,
    broker_url: str,
    display_name: str = "",
    memory_db_path: Path | None = None,
    queue_journal_path: Path | None = None,
    autonomous: bool = False,
    mesh_ports: tuple[int, int] | None = None,
    cluster_file: Path | None = None,
    base: dict[str, Any] | None = None,
) -> str:
    """Render a room member's daemon config as YAML with a provenance header."""
    config = build_member_config(
        handle=handle,
        broker_url=broker_url,
        display_name=display_name,
        memory_db_path=memory_db_path,
        queue_journal_path=queue_journal_path,
        autonomous=autonomous,
        mesh_ports=mesh_ports,
        cluster_file=cluster_file,
        base=base,
    )
    header = (
        f"# Ravn daemon config — member {handle!r} of room {room_name!r}\n"
        f"# Persona: {persona}\n"
        "# Generated by: ravn join\n"
        "# Edit as needed; 'ravn join' regenerates this file with --force.\n"
    )
    return header + yaml.safe_dump(config, sort_keys=False)
