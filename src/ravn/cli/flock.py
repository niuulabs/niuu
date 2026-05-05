"""ravn flock — local multi-node Ravn mesh supervisor.

Manages a set of ``ravn daemon`` child processes, each with a distinct
persona and unique nng ports, forming a local Pi-mode mesh for development
and testing.

Lifecycle
---------
  ravn flock init [PERSONAS...]  — create flock definition + per-node configs
  ravn flock start               — spawn daemons from the existing definition
  ravn flock stop                — graceful shutdown; preserves definition
  ravn flock status              — show live/dead status table
  ravn flock list                — list available personas
  ravn flock peers               — list verified flock members (via node 0)
  ravn flock logs [--node NAME]  — tail logs for one or all nodes

State files
-----------
  flock.yaml   — flock definition (created by init, never touched by start/stop)
  state.json   — runtime PIDs (created by start, deleted by stop)
  node-*.yaml  — per-node daemon configs (created by init)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import typer

from niuu.mesh import nng_gateway_port_for, nng_ports_for
from niuu.mesh.ipc import cleanup_ravn_mesh_sockets, flock_socket_dir, ravn_mesh_addresses

# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------

flock_app = typer.Typer(
    name="flock",
    help="Manage a local multi-node Ravn flock (mesh of daemon processes).",
    add_completion=False,
)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_PERSONAS = ["coordinator", "coding-agent", "research-agent"]
_DEFAULT_BASE_PORT = 7480
_SPAWN_STAGGER_S = 0.5  # seconds between node spawns


def _flock_dir_default() -> Path:
    return Path.home() / ".ravn" / "flock"


# ---------------------------------------------------------------------------
# Node definition  (persisted in flock.yaml — the source of truth)
# ---------------------------------------------------------------------------


@dataclass
class NodeDef:
    """Static node definition — created by init, read by start."""

    index: int
    persona: str
    peer_id: str
    pub_port: int
    rep_port: int
    handshake_port: int
    gateway_port: int
    config_path: str
    log_path: str


@dataclass
class FlockDef:
    """Flock definition — persisted in flock.yaml, created by init."""

    base_port: int
    discovery: str = "mdns"
    mesh_transport: str = "tcp"
    http_gateway_enabled: bool = True
    nodes: list[NodeDef] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_yaml(self) -> str:
        lines = [
            "# Ravn flock definition",
            "# Edit freely, then run: ravn flock start",
            "#",
            f"# Port layout (base_port={self.base_port}):",
            "#   pub       = base + index*2",
            "#   rep       = base + index*2 + 1",
            "#   handshake = base + 100 + index",
            "#   gateway   = base + 200 + index  (HTTP + WebSocket)",
            f"base_port: {self.base_port}",
            f"discovery: {self.discovery}",
            f"mesh_transport: {self.mesh_transport}",
            f"http_gateway_enabled: {str(self.http_gateway_enabled).lower()}",
            "nodes:",
        ]
        for n in self.nodes:
            lines += [
                f"  - index: {n.index}",
                f"    persona: {n.persona}",
                f"    peer_id: {n.peer_id}",
                f"    pub_port: {n.pub_port}",
                f"    rep_port: {n.rep_port}",
                f"    handshake_port: {n.handshake_port}",
                f"    gateway_port: {n.gateway_port}",
                f"    config_path: {n.config_path}",
                f"    log_path: {n.log_path}",
            ]
        return "\n".join(lines) + "\n"

    @classmethod
    def from_yaml(cls, text: str) -> FlockDef:
        import yaml  # pydantic-settings[yaml] dep

        raw = yaml.safe_load(text)
        nodes = [NodeDef(**n) for n in raw.get("nodes", [])]
        return cls(
            base_port=raw["base_port"],
            discovery=raw.get("discovery", "mdns"),
            mesh_transport=raw.get("mesh_transport", "tcp"),
            http_gateway_enabled=bool(raw.get("http_gateway_enabled", True)),
            nodes=nodes,
        )


def _flock_def_path(flock_dir: Path) -> Path:
    return flock_dir / "flock.yaml"


def _load_flock_def(flock_dir: Path) -> FlockDef | None:
    path = _flock_def_path(flock_dir)
    if not path.exists():
        return None
    return FlockDef.from_yaml(path.read_text())


def _save_flock_def(flock_def: FlockDef, flock_dir: Path) -> None:
    path = _flock_def_path(flock_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(flock_def.to_yaml())


# ---------------------------------------------------------------------------
# Runtime state  (persisted in state.json — created by start, deleted by stop)
# ---------------------------------------------------------------------------


@dataclass
class FlockRuntime:
    """Runtime state — maps persona name → PID."""

    started_at: str
    pids: dict[str, int]

    def to_json(self) -> str:
        return json.dumps({"started_at": self.started_at, "pids": self.pids}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> FlockRuntime:
        data = json.loads(text)
        return cls(started_at=data["started_at"], pids=data["pids"])


def _state_path(flock_dir: Path) -> Path:
    return flock_dir / "state.json"


def _load_runtime(flock_dir: Path) -> FlockRuntime | None:
    path = _state_path(flock_dir)
    if not path.exists():
        return None
    return FlockRuntime.from_json(path.read_text())


def _save_runtime(runtime: FlockRuntime, flock_dir: Path) -> None:
    """Write state.json atomically."""
    path = _state_path(flock_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(runtime.to_json())
    os.replace(tmp, path)


def _delete_runtime(flock_dir: Path) -> None:
    _state_path(flock_dir).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Port helpers
# ---------------------------------------------------------------------------


_ports_for = nng_ports_for
_gateway_port_for = nng_gateway_port_for


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _check_ports(flock_def: FlockDef) -> list[int]:
    """Return a list of ports that are already in use."""
    taken = []
    for n in flock_def.nodes:
        ports: list[int] = []
        if flock_def.mesh_transport == "tcp":
            ports.extend([n.pub_port, n.rep_port])
        if flock_def.discovery != "static":
            ports.append(n.handshake_port)
        if flock_def.http_gateway_enabled:
            ports.append(n.gateway_port)
        for port in ports:
            if not _port_free(port):
                taken.append(port)
    return taken


# ---------------------------------------------------------------------------
# Process liveness
# ---------------------------------------------------------------------------


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _any_runtime_alive(runtime: FlockRuntime) -> bool:
    return any(_is_alive(pid) for pid in runtime.pids.values())


# ---------------------------------------------------------------------------
# Config file generation  (called by init)
# ---------------------------------------------------------------------------


def _write_node_config(
    node: NodeDef,
    flock_dir: Path,
    *,
    discovery: str = "mdns",
    mesh_transport: str = "tcp",
    http_gateway_enabled: bool = True,
) -> None:
    """Write the per-node ravn daemon config file."""
    config_path = Path(node.config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    flock_socket_dir(flock_dir).mkdir(parents=True, exist_ok=True)

    if mesh_transport == "ipc":
        pub_address, rep_address = ravn_mesh_addresses(flock_dir, node.persona)
    else:
        pub_address = f"tcp://0.0.0.0:{node.pub_port}"
        rep_address = f"tcp://0.0.0.0:{node.rep_port}"

    if discovery == "static":
        discovery_block = (
            f"discovery:\n"
            f"  enabled: true\n"
            f"  adapters:\n"
            f"    - adapter: ravn.adapters.discovery.static.StaticDiscoveryAdapter\n"
            f'      cluster_file: "{flock_dir}/cluster.yaml"\n'
            f"      poll_interval_s: 5\n"
        )
    else:
        discovery_block = (
            f"discovery:\n"
            f"  enabled: true\n"
            f"  adapter: mdns\n"
            f"  mdns:\n"
            f"    handshake_port: {node.handshake_port}\n"
        )

    config_path.write_text(
        f"""\
# Ravn node config — node {node.index}: {node.persona}
# Generated by: ravn flock init
# Edit as needed. Re-run init with --force to regenerate from defaults.

llm:
  model: Qwen/Qwen3-Coder-30B-A3B-Instruct
  max_tokens: 8192
  timeout: 300.0
  provider:
    adapter: ravn.adapters.llm.openai.OpenAICompatibleAdapter
    kwargs:
      base_url: "https://vllm.valaskjalf.asgard.niuu.world"
      api_key: ""

mesh:
  enabled: true
  adapter: nng
  own_peer_id: "{node.peer_id}"
  nng:
    pub_sub_address: "{pub_address}"
    req_rep_address: "{rep_address}"

{discovery_block}
cascade:
  enabled: true

gateway:
  enabled: {"true" if http_gateway_enabled else "false"}
  channels:
    http:
      enabled: {"true" if http_gateway_enabled else "false"}
      host: "127.0.0.1"
{f"      port: {node.gateway_port}" if http_gateway_enabled else ""}

initiative:
  enabled: true
  max_concurrent_tasks: 3
  queue_journal_path: "{flock_dir}/{node.persona}-queue.json"

memory:
  backend: sqlite
  sqlite:
    path: "{flock_dir}/{node.persona}.db"

logging:
  level: INFO
"""
    )


def _write_cluster_yaml(
    nodes: list[NodeDef], flock_dir: Path, *, mesh_transport: str = "tcp"
) -> None:
    """Write a cluster.yaml for static discovery with all node peers."""
    lines = ["# Static peer definitions (auto-generated by ravn flock init)", "peers:"]
    for node in nodes:
        if mesh_transport == "ipc":
            pub_address, rep_address = ravn_mesh_addresses(flock_dir, node.persona)
        else:
            pub_address = f"tcp://127.0.0.1:{node.pub_port}"
            rep_address = f"tcp://127.0.0.1:{node.rep_port}"
        lines.extend(
            [
                f"  - peer_id: {node.peer_id}",
                f"    persona: {node.persona}",
                f"    display_name: {node.persona}",
                f'    pub_address: "{pub_address}"',
                f'    rep_address: "{rep_address}"',
            ]
        )
    cluster_path = flock_dir / "cluster.yaml"
    cluster_path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Spawn / teardown
# ---------------------------------------------------------------------------


def _spawn_node(node: NodeDef, flock_dir: Path) -> int:
    """Start the daemon process and return its PID."""
    log_path = Path(node.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as log_fd:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ravn", "daemon", "--persona", node.persona],
            env={**os.environ, "RAVN_CONFIG": node.config_path},
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach from parent's process group
        )
    # Parent's fd is closed by context manager; child keeps its inherited copy
    return proc.pid


def _stop_pids(pids: list[int], *, timeout_s: float = 5.0) -> None:
    """Send SIGTERM to all pids, then SIGKILL stragglers."""
    for pid in pids:
        if _is_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not any(_is_alive(pid) for pid in pids):
            return
        time.sleep(0.2)

    for pid in pids:
        if _is_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@flock_app.command("init")
def flock_init(
    personas: list[str] = typer.Argument(
        default=None,
        help="Persona names. Defaults to: coordinator coding-agent research-agent.",
    ),
    base_port: int = typer.Option(
        _DEFAULT_BASE_PORT,
        "--base-port",
        help="First nng port. Ports are allocated sequentially per node.",
    ),
    flock_dir: str = typer.Option("", "--flock-dir", help="Override flock state directory."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing definition."),
    discovery: str = typer.Option(
        "mdns",
        "--discovery",
        help="Discovery method: mdns (default) or static (cluster.yaml, no mDNS).",
    ),
    mesh_transport: str = typer.Option(
        "tcp",
        "--mesh-transport",
        help="Mesh transport: tcp (default) or ipc (Unix socket files).",
    ),
    http_gateway: bool = typer.Option(
        True,
        "--http-gateway/--no-http-gateway",
        help="Enable per-node HTTP/WebSocket gateways.",
    ),
) -> None:
    """Initialise a flock definition without starting any processes.

    \b
    Creates:
      flock.yaml        — flock definition (edit to customise)
      node-*.yaml       — per-node daemon configs (edit to customise)
      logs/             — log directory

    \b
    Examples:
      ravn flock init
      ravn flock init coordinator coding-agent research-agent
      ravn flock init --discovery static   # use cluster.yaml instead of mDNS
      ravn flock init --base-port 8480 coordinator coding-agent
      ravn flock init --force   # regenerate from defaults
    """
    resolved_dir = Path(flock_dir) if flock_dir else _flock_dir_default()
    resolved_personas = list(personas) if personas else list(_DEFAULT_PERSONAS)

    if mesh_transport not in {"tcp", "ipc"}:
        typer.echo("Unsupported mesh transport. Use 'tcp' or 'ipc'.", err=True)
        raise typer.Exit(1)
    if mesh_transport == "ipc" and discovery != "static":
        typer.echo("IPC mesh transport requires --discovery static.", err=True)
        raise typer.Exit(1)

    existing_def = _load_flock_def(resolved_dir)
    if existing_def is not None and not force:
        typer.echo(
            f"Flock already initialised at {resolved_dir}. "
            "Use --force to overwrite, or edit flock.yaml directly.",
            err=True,
        )
        raise typer.Exit(1)

    # Validate personas exist before writing anything.
    from ravn.adapters.personas.loader import FilesystemPersonaAdapter  # noqa: PLC0415

    loader = FilesystemPersonaAdapter()
    for persona in resolved_personas:
        if loader.load(persona) is None:
            typer.echo(
                f"Unknown persona {persona!r}. Run 'ravn flock list' to see available personas.",
                err=True,
            )
            raise typer.Exit(1)

    resolved_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = resolved_dir / "logs"
    logs_dir.mkdir(exist_ok=True)

    # Build node definitions.
    nodes: list[NodeDef] = []
    for i, persona in enumerate(resolved_personas):
        pub, rep, hs = _ports_for(i, base_port)
        gw = _gateway_port_for(i, base_port)
        nodes.append(
            NodeDef(
                index=i,
                persona=persona,
                peer_id=f"flock-{persona}",
                pub_port=pub,
                rep_port=rep,
                handshake_port=hs,
                gateway_port=gw,
                config_path=str(resolved_dir / f"node-{persona}.yaml"),
                log_path=str(logs_dir / f"{persona}.log"),
            )
        )

    flock_def = FlockDef(
        base_port=base_port,
        discovery=discovery,
        mesh_transport=mesh_transport,
        http_gateway_enabled=http_gateway,
        nodes=nodes,
    )
    _save_flock_def(flock_def, resolved_dir)

    for node in nodes:
        _write_node_config(
            node,
            resolved_dir,
            discovery=discovery,
            mesh_transport=mesh_transport,
            http_gateway_enabled=http_gateway,
        )

    if discovery == "static":
        _write_cluster_yaml(nodes, resolved_dir, mesh_transport=mesh_transport)

    typer.echo(f"Flock initialised at {resolved_dir}")
    typer.echo("")
    for node in nodes:
        summary = f"  [{node.index}] {node.persona:<22} mesh={mesh_transport}"
        if http_gateway:
            summary += f"  http=127.0.0.1:{node.gateway_port}  ws=127.0.0.1:{node.gateway_port}/ws"
        else:
            summary += "  http=disabled"
        typer.echo(summary)
    typer.echo("")
    typer.echo(f"  Definition:  {resolved_dir}/flock.yaml")
    typer.echo(f"  Node configs: {resolved_dir}/node-*.yaml")
    typer.echo("")
    typer.echo("Edit flock.yaml or node configs as needed, then:")
    typer.echo("  ravn flock start")


@flock_app.command("start")
def flock_start(
    flock_dir: str = typer.Option("", "--flock-dir", help="Override flock state directory."),
) -> None:
    """Start daemons from an existing flock definition.

    \b
    Run 'ravn flock init' first to create the definition.

    \b
    Examples:
      ravn flock start
      ravn flock start --flock-dir /path/to/my-flock
    """
    resolved_dir = Path(flock_dir) if flock_dir else _flock_dir_default()

    flock_def = _load_flock_def(resolved_dir)
    if flock_def is None:
        typer.echo(
            f"No flock definition found at {resolved_dir}. Run 'ravn flock init' first.",
            err=True,
        )
        raise typer.Exit(1)

    # Guard: refuse if a live flock is already running.
    runtime = _load_runtime(resolved_dir)
    if runtime is not None and _any_runtime_alive(runtime):
        typer.echo("A flock is already running. Run 'ravn flock stop' first.", err=True)
        raise typer.Exit(1)

    # Pre-flight: verify all required ports are free.
    taken = _check_ports(flock_def)
    if taken:
        typer.echo(
            f"Ports already in use: {taken}. "
            "Edit flock.yaml to change ports, or stop whatever is using them.",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Starting flock ({len(flock_def.nodes)} nodes)…")
    typer.echo(f"  Definition: {resolved_dir}/flock.yaml")
    typer.echo("")

    if flock_def.mesh_transport == "ipc":
        cleanup_ravn_mesh_sockets(resolved_dir, [node.persona for node in flock_def.nodes])

    # Spawn workers first, coordinator last, so workers are announcing before
    # the coordinator starts looking for peers.
    spawn_order = list(range(1, len(flock_def.nodes))) + [0]
    pids: dict[str, int] = {}

    for i in spawn_order:
        node = flock_def.nodes[i]
        pid = _spawn_node(node, resolved_dir)
        pids[node.persona] = pid
        if flock_def.mesh_transport == "ipc":
            pub_address, rep_address = ravn_mesh_addresses(resolved_dir, node.persona)
            summary = (
                f"  [{node.index}] {node.persona:<22} pid={pid}  "
                f"pub={pub_address}  rep={rep_address}"
            )
        else:
            summary = (
                f"  [{node.index}] {node.persona:<22} pid={pid}  "
                f"pub={node.pub_port}  rep={node.rep_port}"
            )
        if flock_def.discovery != "static":
            summary += f"  hs={node.handshake_port}"
        if flock_def.http_gateway_enabled:
            summary += f"  http=127.0.0.1:{node.gateway_port}"
        else:
            summary += "  http=disabled"
        typer.echo(summary)
        if i != spawn_order[-1]:
            time.sleep(_SPAWN_STAGGER_S)

    _save_runtime(
        FlockRuntime(started_at=datetime.now(UTC).isoformat(), pids=pids),
        resolved_dir,
    )

    typer.echo("")
    if flock_def.discovery == "static":
        typer.echo("Flock started. Nodes will discover each other via static cluster.yaml.")
    else:
        typer.echo("Flock started. Nodes will discover each other via mDNS (~3s).")
    typer.echo("")
    if flock_def.http_gateway_enabled:
        typer.echo("HTTP / WebSocket endpoints:")
        for node in flock_def.nodes:
            typer.echo(
                f"  {node.persona:<22} "
                f"http://127.0.0.1:{node.gateway_port}/chat  "
                f"ws://127.0.0.1:{node.gateway_port}/ws"
            )
        typer.echo("")
    typer.echo("  ravn flock status   — check health")
    typer.echo("  ravn flock peers    — list verified flock members")
    typer.echo("  ravn flock logs     — tail all logs")
    typer.echo("  ravn flock stop     — shut everything down")


@flock_app.command("stop")
def flock_stop(
    flock_dir: str = typer.Option("", "--flock-dir", help="Override flock state directory."),
) -> None:
    """Stop all running flock nodes. Preserves flock.yaml and node configs."""
    resolved_dir = Path(flock_dir) if flock_dir else _flock_dir_default()
    runtime = _load_runtime(resolved_dir)
    if runtime is None:
        typer.echo("No running flock found (no state.json).")
        return

    pids = list(runtime.pids.values())
    typer.echo(f"Stopping {len(pids)} node(s)…")
    _stop_pids(pids)
    if flock_def := _load_flock_def(resolved_dir):
        if flock_def.mesh_transport == "ipc":
            cleanup_ravn_mesh_sockets(resolved_dir, [node.persona for node in flock_def.nodes])
    _delete_runtime(resolved_dir)
    typer.echo("Flock stopped. Definition preserved — run 'ravn flock start' to restart.")


@flock_app.command("status")
def flock_status(
    flock_dir: str = typer.Option("", "--flock-dir", help="Override flock state directory."),
) -> None:
    """Show the status of each flock node."""
    resolved_dir = Path(flock_dir) if flock_dir else _flock_dir_default()

    flock_def = _load_flock_def(resolved_dir)
    if flock_def is None:
        typer.echo("No flock definition found. Run 'ravn flock init' first.")
        return

    runtime = _load_runtime(resolved_dir)
    typer.echo(f"Flock  ({len(flock_def.nodes)} nodes)  base-port={flock_def.base_port}")
    typer.echo("")
    for node in flock_def.nodes:
        pid = runtime.pids.get(node.persona, 0) if runtime else 0
        if pid and _is_alive(pid):
            status_tag = f"running  pid={pid}"
        elif pid:
            status_tag = f"DEAD     pid={pid}"
        else:
            status_tag = "stopped"
        summary = f"  [{node.index}] {node.persona:<22} "
        if flock_def.http_gateway_enabled:
            summary += f"http=127.0.0.1:{node.gateway_port}  "
        else:
            summary += "http=disabled  "
        summary += f"[{status_tag}]"
        typer.echo(summary)


@flock_app.command("list")
def flock_list() -> None:
    """List available personas (built-in and user-defined)."""
    from ravn.adapters.personas.loader import FilesystemPersonaAdapter  # noqa: PLC0415

    loader = FilesystemPersonaAdapter()
    builtin_names = set(loader.list_builtin_names())
    all_names = sorted(loader.list_names())

    typer.echo("Available personas:")
    typer.echo("")
    for name in all_names:
        persona = loader.load(name)
        source = "[built-in]" if name in builtin_names else f"[~/.ravn/personas/{name}.yaml]"
        allowed = ", ".join(persona.allowed_tools) if persona and persona.allowed_tools else "all"
        typer.echo(f"  {name:<24} {source:<40}  tools: {allowed}")


@flock_app.command("peers")
def flock_peers(
    node: int = typer.Option(
        0,
        "--node",
        "-n",
        help="Node index to query (default: 0, the first node).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show address and latency."),
    flock_dir: str = typer.Option("", "--flock-dir", help="Override flock state directory."),
) -> None:
    """List verified flock members via the selected node's discovery config."""
    resolved_dir = Path(flock_dir) if flock_dir else _flock_dir_default()

    flock_def = _load_flock_def(resolved_dir)
    if flock_def is None:
        typer.echo("No flock definition found.", err=True)
        raise typer.Exit(1)

    runtime = _load_runtime(resolved_dir)
    if runtime is None:
        typer.echo("No running flock found.", err=True)
        raise typer.Exit(1)

    # Pick the requested node; fall back to first alive if it is dead.
    target = next((n for n in flock_def.nodes if n.index == node), None)
    if target is None or not _is_alive(runtime.pids.get(target.persona, 0)):
        target = next(
            (n for n in flock_def.nodes if _is_alive(runtime.pids.get(n.persona, 0))),
            None,
        )
    if target is None:
        typer.echo("No live flock nodes found.", err=True)
        raise typer.Exit(1)

    # Lazy import to avoid circular dependency at module load time.
    from ravn.cli.commands import _run_peers  # noqa: PLC0415
    from ravn.config import Settings  # noqa: PLC0415

    os.environ["RAVN_CONFIG"] = target.config_path
    settings = Settings()
    asyncio.run(_run_peers(settings, verbose=verbose, force_scan=False))


@flock_app.command("logs")
def flock_logs(
    node: str = typer.Option(
        "",
        "--node",
        "-n",
        help="Persona name or node index to tail. Defaults to all nodes.",
    ),
    follow: bool = typer.Option(True, "--follow/--no-follow", "-f/-F", help="Follow log output."),
    lines: int = typer.Option(20, "--lines", "-l", help="Number of initial lines to show."),
    flock_dir: str = typer.Option("", "--flock-dir", help="Override flock state directory."),
) -> None:
    """Tail logs for one or all flock nodes."""
    resolved_dir = Path(flock_dir) if flock_dir else _flock_dir_default()

    flock_def = _load_flock_def(resolved_dir)
    if flock_def is None:
        typer.echo("No flock definition found.", err=True)
        raise typer.Exit(1)

    # Resolve which nodes to tail.
    target_nodes: list[NodeDef]
    if not node:
        target_nodes = flock_def.nodes
    elif node.isdigit():
        idx = int(node)
        target_nodes = [n for n in flock_def.nodes if n.index == idx]
    else:
        target_nodes = [n for n in flock_def.nodes if n.persona == node]

    if not target_nodes:
        typer.echo(f"No node matching {node!r}.", err=True)
        raise typer.Exit(1)

    log_paths = [n.log_path for n in target_nodes if Path(n.log_path).exists()]
    if not log_paths:
        typer.echo("No log files found yet.", err=True)
        raise typer.Exit(1)

    tail_bin = _find_tail()
    if tail_bin:
        cmd = [tail_bin, f"-n{lines}"]
        if follow:
            cmd.append("-f")
        cmd.extend(log_paths)
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            pass
        return

    _python_tail(log_paths, lines=lines, follow=follow)


# ---------------------------------------------------------------------------
# Internal helpers for flock logs
# ---------------------------------------------------------------------------


def _find_tail() -> str | None:
    for candidate in ("/usr/bin/tail", "/bin/tail"):
        if Path(candidate).exists():
            return candidate
    return None


def _python_tail(paths: list[str], *, lines: int, follow: bool) -> None:
    """Minimal pure-Python tail — prints last *lines* then optionally follows."""
    with contextlib.ExitStack() as stack:
        handles = []
        for p in paths:
            try:
                handles.append((p, stack.enter_context(open(p))))
            except OSError:
                pass

        try:
            for path, fh in handles:
                content = fh.read().splitlines()
                header = f"==> {path} <=="
                typer.echo(header)
                for line in content[-lines:]:
                    typer.echo(line)

            if not follow:
                return

            while True:
                for path, fh in handles:
                    chunk = fh.read()
                    if chunk:
                        typer.echo(chunk, nl=False)
                time.sleep(0.25)
        except KeyboardInterrupt:
            pass
