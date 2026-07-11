"""Local process adapter for pod management.

Runs Claude Code as a local subprocess instead of Kubernetes pods.
Implements PodManager so Volundr's SessionService, REST endpoints,
and event pipeline work unchanged — only the pod manager swaps from
K8s to local process.

Constructor accepts plain kwargs (dynamic adapter pattern):
    adapter: "volundr.adapters.outbound.local_process.LocalProcessPodManager"
    workspaces_dir: "~/.niuu/workspaces"
    claude_binary: "claude"
    max_concurrent: 4
    sdk_port_start: 9100
    stop_timeout: 10
    state_file: "~/.niuu/forge-state.json"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from niuu.domain.llm_merge import merge_llm
from niuu.mesh.ipc import cleanup_skuld_mesh_sockets, skuld_mesh_addresses
from volundr.adapters.outbound.contributors.ravn_flock import (
    _MIMIR_MOUNT_PATH as _FLOCK_MIMIR_MOUNT_PATH,
)
from volundr.adapters.outbound.contributors.ravn_flock import (
    _resolve_mimir_runtime as _resolve_flock_mimir_runtime,
)
from volundr.domain.models import (
    GitSource,
    LocalMountSource,
    Session,
    SessionSpec,
    SessionStatus,
)
from volundr.domain.ports import PodManager, PodStartResult

logger = logging.getLogger(__name__)

# Default configuration values
DEFAULT_WORKSPACES_DIR = "~/.niuu/workspaces"
DEFAULT_CLAUDE_BINARY = "claude"
# Capable hosts run many sessions comfortably (each ~0.5 GB); the practical
# ceiling is the Claude/Codex *subscription* concurrency, not the box. Deployments
# override this via pod_manager.max_concurrent in config.yaml.
DEFAULT_MAX_CONCURRENT = 16
DEFAULT_SDK_PORT_START = 9100
DEFAULT_STOP_TIMEOUT = 10
DEFAULT_STATE_FILE = "~/.niuu/forge-state.json"
DEFAULT_FLOCK_BASE_PORT = 7480
DEFAULT_FLOCK_PORT_SCAN_LIMIT = 1000
DEFAULT_ALLOWED_MOUNT_PREFIXES: list[str] = []

# Poll interval for wait_for_ready
READY_POLL_INTERVAL = 0.5


def _public_loopback_host(host: str) -> str:
    """Normalize the configured host published to browser-facing clients."""
    host = host.strip() or "127.0.0.1"
    return "localhost" if host == "127.0.0.1" else host


class ProcessState(StrEnum):
    """State of a managed Claude process."""

    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class ProcessInfo:
    """Tracked state for a managed Claude process."""

    session_id: str
    pid: int | None = None
    port: int | None = None
    workspace: str = ""
    state: ProcessState = ProcessState.STARTING
    error: str | None = None
    flock_dir: str = ""
    flock_base_port: int | None = None
    managed_by: str = "local_process"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ProcessInfo:
        """Deserialize from dict."""
        # session_id may be stored as "session_id" (Python) or "id" (Go legacy)
        session_id = data.get("session_id") or data.get("id", "")
        return ProcessInfo(
            session_id=session_id,
            pid=data.get("pid"),
            port=data.get("port"),
            workspace=data.get("workspace", data.get("workspace_dir", "")),
            state=ProcessState(data.get("state", data.get("status", "stopped"))),
            error=data.get("error"),
            flock_dir=data.get("flock_dir", ""),
            flock_base_port=data.get("flock_base_port"),
            managed_by=data.get("managed_by", "local_process"),
        )


@dataclass(frozen=True)
class FlockPortPlan:
    """Per-session local mesh ports shared by Skuld and Ravn sidecars."""

    session_base_port: int
    ravn_base_port: int
    skuld_pub_port: int
    skuld_rep_port: int
    skuld_handshake_port: int


class SdkPortAllocator:
    """Allocates SDK ports from a configurable range.

    Tracks allocated ports and verifies availability via socket bind test.
    """

    def __init__(self, start_port: int = DEFAULT_SDK_PORT_START):
        self._start_port = start_port
        self._allocated: set[int] = set()
        self._next_port = start_port

    @property
    def allocated(self) -> set[int]:
        """Currently allocated ports."""
        return set(self._allocated)

    def allocate(self) -> int:
        """Allocate the next free port.

        Returns:
            An available port number.

        Raises:
            RuntimeError: If no free port can be found after scanning a range.
        """
        scanned = 0
        max_scan = 1000
        while scanned < max_scan:
            port = self._next_port
            self._next_port += 1
            scanned += 1

            if port in self._allocated:
                continue

            if not self._is_port_free(port):
                continue

            self._allocated.add(port)
            return port

        raise RuntimeError(
            f"No free SDK port found after scanning {max_scan} ports from {self._start_port}"
        )

    def release(self, port: int) -> None:
        """Return a port to the pool."""
        self._allocated.discard(port)

    @staticmethod
    def _is_port_free(port: int) -> bool:
        """Check if a port is free for the local flock bind patterns we use.

        Probe loopback only. On macOS a wildcard listener already blocks a
        loopback bind on the same port, which lets us catch ``*:port``
        collisions without briefly binding ``0.0.0.0`` ourselves.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _inject_token_into_url(repo_url: str, token: str) -> str:
    """Rewrite a git URL to include an access token for cloning.

    Supports https://github.com/... and https://gitlab.com/... patterns.
    """
    if not token:
        return repo_url

    pattern = r"^https://(github\.com|gitlab\.com)/"
    if re.match(pattern, repo_url):
        return re.sub(
            r"^https://",
            f"https://x-access-token:{token}@",
            repo_url,
        )

    return repo_url


def _looks_like_local_path(value: str) -> bool:
    trimmed = value.strip()
    return trimmed.startswith(("/", "~", "./", "../"))


def _local_workspace_from_repo(value: str) -> Path | None:
    """Treat filesystem repo values as local workspaces instead of clone sources."""
    if not _looks_like_local_path(value):
        return None
    return Path(value).expanduser()


def _stage_personas(node: dict[str, Any]) -> set[str]:
    personas = {
        str(persona)
        for persona in (node.get("personaIds") or [])
        if isinstance(persona, str) and persona
    }
    for member in node.get("stageMembers") or []:
        if isinstance(member, dict):
            persona_id = member.get("personaId")
            if isinstance(persona_id, str) and persona_id:
                personas.add(persona_id)
    return personas


def _split_workflow_edge_label(label: object) -> tuple[str, str]:
    if not isinstance(label, str) or "->" not in label:
        return "", ""
    left, right = label.split("->", 1)
    return left.strip(), right.strip()


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _workflow_event_metadata(
    workflow: dict[str, Any] | None,
    persona: str,
) -> tuple[list[str], list[str]]:
    """Infer consumed/emitted event types for a persona from the workflow graph."""
    if not isinstance(workflow, dict):
        return [], []
    graph = workflow.get("graph")
    if not isinstance(graph, dict):
        return [], []

    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
    persona_node_ids = {str(node.get("id")) for node in nodes if persona in _stage_personas(node)}
    if not persona_node_ids:
        return [], []

    member_override_events: list[str] = []
    for node in nodes:
        for member in node.get("stageMembers") or []:
            if not isinstance(member, dict) or member.get("personaId") != persona:
                continue
            consumes_event_types = member.get("consumesEventTypes")
            if not isinstance(consumes_event_types, list):
                consumes_event_types = member.get("consumes_event_types")
            if isinstance(consumes_event_types, list):
                member_override_events.extend(
                    str(event_type).strip()
                    for event_type in consumes_event_types
                    if str(event_type).strip()
                )

    consumes: list[str] = []
    emits: list[str] = []
    has_inbound = False

    for edge in edges:
        source_event, target_event = _split_workflow_edge_label(edge.get("label"))
        source_id = str(edge.get("source", ""))
        target_id = str(edge.get("target", ""))

        if target_id in persona_node_ids and source_event:
            consumes.append(source_event)
            has_inbound = True

        if source_id in persona_node_ids and target_event and target_event != "complete":
            emits.append(target_event)

    if not has_inbound:
        trigger_events = [
            str(node.get("dispatchEvent"))
            for node in nodes
            if node.get("kind") == "trigger" and node.get("dispatchEvent")
        ]
        consumes.extend(trigger_events)

    if member_override_events:
        consumes = member_override_events

    return _dedupe_preserve_order(consumes), _dedupe_preserve_order(emits)


def _normalize_local_mimir_spec(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, Any] = {}
    if hosted_url := raw.get("hostedUrl"):
        normalized["hosted_url"] = hosted_url
    if registry_refs := raw.get("registryRefs"):
        normalized["registry_refs"] = registry_refs
    if ephemeral_locals := raw.get("ephemeralLocals"):
        normalized["ephemeral_locals"] = ephemeral_locals
    if bindings := raw.get("bindings"):
        normalized["bindings"] = bindings
    if instances := raw.get("instances"):
        normalized["instances"] = instances
    if write_routing := raw.get("writeRouting"):
        normalized["write_routing"] = write_routing
    if default_mounts := raw.get("defaultMounts"):
        normalized["default_mounts"] = default_mounts
    return normalized


def _localize_mimir_path(path: str, flock_dir: Path) -> str:
    trimmed = path.strip()
    if not trimmed:
        return trimmed
    if trimmed == _FLOCK_MIMIR_MOUNT_PATH:
        return str(flock_dir / "mimir" / "local")
    prefix = f"{_FLOCK_MIMIR_MOUNT_PATH.rstrip('/')}/"
    if trimmed.startswith(prefix):
        suffix = trimmed[len(prefix) :]
        return str((flock_dir / "mimir" / "local" / suffix).resolve())
    return trimmed


def _materialize_local_mimir_config(spec: SessionSpec, flock_dir: Path) -> dict[str, Any] | None:
    mimir_cfg = _normalize_local_mimir_spec(spec.values.get("mimir"))
    if not mimir_cfg:
        return None

    instances, write_routing = _resolve_flock_mimir_runtime(mimir_cfg)
    localized_instances: list[dict[str, Any]] = []
    for raw_instance in instances:
        instance = dict(raw_instance)
        path = instance.get("path")
        if isinstance(path, str) and path.strip():
            localized_path = _localize_mimir_path(path, flock_dir)
            Path(localized_path).mkdir(parents=True, exist_ok=True)
            instance["path"] = localized_path
        elif str(instance.get("url") or "").strip():
            localized_path = flock_dir / "mimir" / "local" / str(instance["name"])
            localized_path.mkdir(parents=True, exist_ok=True)
            instance.pop("url", None)
            instance.pop("auth", None)
            instance["path"] = str(localized_path)
        localized_instances.append(instance)

    return {
        "enabled": True,
        "instances": localized_instances,
        "write_routing": write_routing,
    }


def _localize_mcp_servers(raw_servers: object, workspace: Path) -> list[dict[str, Any]]:
    if not isinstance(raw_servers, list):
        return []

    flock_dir = workspace / ".flock"
    localized: list[dict[str, Any]] = []
    for raw_server in raw_servers:
        if not isinstance(raw_server, dict):
            continue
        server = dict(raw_server)
        args = server.get("args")
        if isinstance(args, list):
            normalized_args = [str(arg) for arg in args]
            rewritten_args: list[str] = []
            index = 0
            while index < len(normalized_args):
                arg = normalized_args[index]
                rewritten_args.append(arg)
                if arg == "--path" and index + 1 < len(normalized_args):
                    localized_path = _localize_mimir_path(
                        normalized_args[index + 1],
                        flock_dir,
                    )
                    rewritten_args.append(localized_path)
                    index += 2
                    continue
                index += 1
            server["args"] = rewritten_args
        localized.append(server)
    return localized


class LocalProcessPodManager(PodManager):
    """Manages Claude Code as local subprocesses.

    Implements PodManager so Volundr's existing SessionService, REST
    endpoints, and event pipeline work unchanged.
    """

    def __init__(
        self,
        *,
        workspaces_dir: str = DEFAULT_WORKSPACES_DIR,
        claude_binary: str = DEFAULT_CLAUDE_BINARY,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        sdk_port_start: int = DEFAULT_SDK_PORT_START,
        stop_timeout: int = DEFAULT_STOP_TIMEOUT,
        state_file: str = DEFAULT_STATE_FILE,
        allowed_mount_prefixes: list[str] | None = None,
        server_public_host: str = "127.0.0.1",
        server_host: str = "127.0.0.1",
        server_port: int = 8080,
        **_extra: object,
    ):
        self._workspaces_dir = Path(str(workspaces_dir)).expanduser()
        self._claude_binary = str(claude_binary)
        self._max_concurrent = int(max_concurrent)
        self._stop_timeout = int(stop_timeout)
        self._state_file = Path(str(state_file)).expanduser()
        self._server_public_host = str(server_public_host)
        self._server_host = str(server_host)
        self._server_port = int(server_port)
        if isinstance(allowed_mount_prefixes, str):
            allowed_mount_prefixes = [
                prefix.strip() for prefix in allowed_mount_prefixes.split(",") if prefix.strip()
            ]
        self._allowed_mount_prefixes = allowed_mount_prefixes or DEFAULT_ALLOWED_MOUNT_PREFIXES

        self._port_allocator = SdkPortAllocator(start_port=int(sdk_port_start))
        self._allocated_flock_base_ports: set[int] = set()
        self._processes: dict[str, ProcessInfo] = {}
        self._monitors: dict[str, asyncio.Task] = {}
        self._skuld_registry: object | None = None  # Set via set_skuld_registry()
        self._persona_registry: object | None = None  # Set via set_persona_registry()
        # Notified (best-effort) when a managed broker process exits, so the
        # SessionService can reconcile the DB row promptly instead of waiting for
        # the periodic sweep. Set via set_death_callback().
        self._death_callback: Callable[[str], Awaitable[None]] | None = None

        self._load_state()
        for info in self._processes.values():
            if info.state in (ProcessState.STARTING, ProcessState.RUNNING) and info.flock_base_port:
                self._allocated_flock_base_ports.add(info.flock_base_port)

    def set_skuld_registry(self, registry: object) -> None:
        """Inject the SkuldPortRegistry for proxy routing."""
        self._skuld_registry = registry
        register = getattr(registry, "register", None)
        if not callable(register):
            return
        for session_id, info in self._processes.items():
            if info.state == ProcessState.RUNNING and info.port is not None:
                register(session_id, info.port)

    def set_persona_registry(self, registry: object) -> None:
        """Inject the persona registry used to materialize custom flock personas."""
        self._persona_registry = registry

    def set_death_callback(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """Inject an async callback notified when a managed broker process exits.

        The callback receives the session id string and is expected to reconcile
        the persisted Session row (pod-status authoritative) so the DB reflects
        the broker's death promptly. Wiring it avoids relying solely on the
        periodic reconcile sweep for locally-managed brokers.
        """
        self._death_callback = callback

    # ------------------------------------------------------------------
    # PodManager interface
    # ------------------------------------------------------------------

    async def start(
        self,
        session: Session,
        spec: SessionSpec,
    ) -> PodStartResult:
        """Provision workspace, spawn Claude process, return endpoints."""
        session_id = str(session.id)

        active = self._reconcile_active()
        if len(active) >= self._max_concurrent:
            detail = ", ".join(
                f"{sid[:8]}:{self._processes[sid].state.value}(pid={self._processes[sid].pid})"
                for sid in active
            )
            logger.warning(
                "Max concurrent reached: %d/%d slots in use [%s] — rejecting new session %s",
                len(active),
                self._max_concurrent,
                detail,
                session_id[:8],
            )
            raise RuntimeError(f"Max concurrent sessions ({self._max_concurrent}) reached")
        logger.info(
            "Provisioning session %s (%d/%d concurrent slots in use)",
            session_id[:8],
            len(active),
            self._max_concurrent,
        )

        workspace = await self._provision_workspace(session, spec)
        port = self._port_allocator.allocate()
        flock_plan: FlockPortPlan | None = None
        if spec.pod_spec and spec.pod_spec.extra_containers:
            persona_count = sum(
                1
                for container in spec.pod_spec.extra_containers
                if container.get("name", "").startswith("ravn-")
            )
            if persona_count > 0:
                flock_plan = self._flock_port_plan(self._pick_flock_base_port(persona_count))

        info = ProcessInfo(
            session_id=session_id,
            port=port,
            workspace=str(workspace),
            state=ProcessState.STARTING,
            flock_base_port=flock_plan.session_base_port if flock_plan is not None else None,
        )
        self._processes[session_id] = info
        self._persist_state()

        try:
            pid = await self._spawn_skuld(
                session,
                spec,
                workspace,
                port,
                flock_plan=flock_plan,
            )
            info.pid = pid
            info.state = ProcessState.RUNNING
            self._persist_state()

            # Spawn ravn flock sidecars if the contributor produced extra containers
            if spec.pod_spec and spec.pod_spec.extra_containers and flock_plan is not None:
                flock_dir = await self._start_flock(
                    session,
                    spec,
                    workspace,
                    flock_plan,
                    skuld_port=port,
                )
                info.flock_dir = str(flock_dir)
                self._persist_state()

            # Register with Skuld port registry for proxy routing
            if self._skuld_registry is not None:
                self._skuld_registry.register(session_id, port)

            monitor = asyncio.create_task(
                self._monitor_process(session_id, pid),
                name=f"monitor-{session_id}",
            )
            self._monitors[session_id] = monitor

        except Exception:
            info.state = ProcessState.FAILED
            self._port_allocator.release(port)
            if info.flock_base_port is not None:
                self._allocated_flock_base_ports.discard(info.flock_base_port)
            self._persist_state()
            raise

        # Chat endpoint routes through the root server's proxy
        server_host = _public_loopback_host(self._server_public_host)
        server_port = self._server_port
        chat_endpoint = f"ws://{server_host}:{server_port}/s/{session_id}/session"
        code_endpoint = f"file://{workspace}"
        pod_name = f"local-{session_id[:8]}"

        return PodStartResult(
            chat_endpoint=chat_endpoint,
            code_endpoint=code_endpoint,
            pod_name=pod_name,
        )

    async def stop(self, session: Session) -> bool:
        """Stop the Skuld process for a session."""
        session_id = str(session.id)
        info = self._processes.get(session_id)

        if info is None:
            return False

        if info.state not in (ProcessState.RUNNING, ProcessState.STARTING):
            return True

        monitor = self._monitors.pop(session_id, None)
        if monitor and not monitor.done():
            monitor.cancel()

        # Stop flock sidecars first (they depend on the mesh)
        if info.flock_dir:
            self._stop_flock(info.flock_dir)

        if info.pid is not None:
            await self._terminate_process(info.pid)

        if info.port is not None:
            self._port_allocator.release(info.port)
        if info.flock_base_port is not None:
            self._allocated_flock_base_ports.discard(info.flock_base_port)

        if self._skuld_registry is not None:
            self._skuld_registry.unregister(session_id)

        info.state = ProcessState.STOPPED
        self._persist_state()
        return True

    async def status(self, session: Session) -> SessionStatus:
        """Get the current status of the local Claude process."""
        session_id = str(session.id)
        info = self._processes.get(session_id)

        if info is None:
            return SessionStatus.STOPPED

        match info.state:
            case ProcessState.STARTING:
                return SessionStatus.PROVISIONING
            case ProcessState.RUNNING:
                return SessionStatus.RUNNING
            case ProcessState.STOPPED:
                return SessionStatus.STOPPED
            case ProcessState.FAILED:
                return SessionStatus.FAILED
        raise AssertionError("Unreachable status fallthrough")

    async def wait_for_ready(self, session: Session, timeout: float) -> SessionStatus:
        """Wait until the Claude process is running or fails."""
        session_id = str(session.id)
        elapsed = 0.0

        while elapsed < timeout:
            info = self._processes.get(session_id)

            if info is None:
                return SessionStatus.FAILED

            if info.state == ProcessState.RUNNING:
                return SessionStatus.RUNNING

            if info.state == ProcessState.FAILED:
                return SessionStatus.FAILED

            if info.state == ProcessState.STOPPED:
                return SessionStatus.STOPPED

            await asyncio.sleep(READY_POLL_INTERVAL)
            elapsed += READY_POLL_INTERVAL

        return SessionStatus.FAILED

    # ------------------------------------------------------------------
    # Workspace provisioning
    # ------------------------------------------------------------------

    async def _provision_workspace(
        self,
        session: Session,
        spec: SessionSpec,
    ) -> Path:
        """Create workspace directory and set up source code."""
        if isinstance(session.source, LocalMountSource) and session.source.local_path:
            # Mini/local mode: use the directory directly (matches Go CLI behavior)
            workspace = Path(session.source.local_path)
            if not workspace.is_dir():
                raise RuntimeError(f"local path {workspace!r} is not a directory")
            if not self._is_allowed_mount(workspace):
                raise RuntimeError(
                    f"local path {workspace!r} is not under any allowed mount prefix"
                )
            self._write_claude_md(workspace, spec)
            return workspace
        if isinstance(session.source, GitSource) and session.source.repo:
            local_workspace = _local_workspace_from_repo(session.source.repo)
            if local_workspace is not None:
                if not local_workspace.is_dir():
                    raise RuntimeError(f"local repo path {local_workspace!r} is not a directory")
                workspace = local_workspace.resolve()
                self._write_claude_md(workspace, spec)
                return workspace

        workspace = self._workspaces_dir / str(session.id)
        workspace.mkdir(parents=True, exist_ok=True)

        if isinstance(session.source, GitSource) and session.source.repo:
            await self._clone_repo(session.source, workspace, spec)
        elif isinstance(session.source, LocalMountSource) and session.source.paths:
            self._setup_local_mounts(session.source, workspace)

        self._write_claude_md(workspace, spec)
        return workspace

    async def _clone_repo(
        self,
        source: GitSource,
        workspace: Path,
        spec: SessionSpec,
    ) -> None:
        """Clone a git repository into the workspace."""
        git_cfg = spec.values.get("git", {})
        token = spec.values.get("git_token", "")
        clone_url = str(git_cfg.get("cloneUrl") or "").strip() or _inject_token_into_url(
            source.repo,
            token,
        )
        repo_url = str(git_cfg.get("repoUrl") or "").strip() or source.repo
        branch = str(git_cfg.get("branch") or source.branch or "").strip()
        base_branch = str(git_cfg.get("baseBranch") or source.base_branch or "").strip()

        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth",
            "1",
            "--no-single-branch",
            clone_url,
            str(workspace / "repo"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode(errors="replace")
            error_msg = re.sub(r"://[^@]+@", "://***@", error_msg)
            raise RuntimeError(f"Git clone failed: {error_msg}")

        repo_dir = workspace / "repo"
        if branch:
            checkout_ok = await self._checkout_tracking_branch(repo_dir, branch)
            if not checkout_ok:
                fallback_branch = base_branch or await self._remote_head_branch(repo_dir)
                if fallback_branch:
                    fallback_ok = await self._checkout_tracking_branch(repo_dir, fallback_branch)
                    if fallback_ok:
                        await self._create_branch(repo_dir, branch)

        if repo_url:
            await self._run_git(repo_dir, "remote", "set-url", "origin", repo_url)
            if clone_url != repo_url:
                await self._run_git(repo_dir, "remote", "set-url", "--push", "origin", clone_url)

    async def _run_git(self, repo_dir: Path, *args: str) -> tuple[int, str, str]:
        """Run a git command in *repo_dir* and return (code, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repo_dir),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (
            proc.returncode,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )

    async def _checkout_tracking_branch(self, repo_dir: Path, branch: str) -> bool:
        """Check out *branch* from ``origin/<branch>`` when it exists."""
        code, _, _ = await self._run_git(repo_dir, "rev-parse", "--verify", f"origin/{branch}")
        if code != 0:
            return False
        code, _, _ = await self._run_git(repo_dir, "checkout", "-B", branch, f"origin/{branch}")
        return code == 0

    async def _create_branch(self, repo_dir: Path, branch: str) -> bool:
        """Create a new local branch from the current HEAD."""
        code, _, _ = await self._run_git(repo_dir, "checkout", "-b", branch)
        return code == 0

    async def _remote_head_branch(self, repo_dir: Path) -> str:
        """Return the remote HEAD branch name when available."""
        code, stdout, _ = await self._run_git(
            repo_dir,
            "symbolic-ref",
            "--short",
            "refs/remotes/origin/HEAD",
        )
        if code != 0:
            return ""
        value = stdout.strip()
        if value.startswith("origin/"):
            return value.removeprefix("origin/")
        return value

    def _setup_local_mounts(
        self,
        source: LocalMountSource,
        workspace: Path,
    ) -> None:
        """Create symlinks for local mount sources."""
        for mapping in source.paths:
            host = Path(mapping.host_path)

            if not self._is_allowed_mount(host):
                logger.warning("Skipping mount %s: not in allowed prefixes", host)
                continue

            if not host.exists():
                logger.warning("Skipping mount %s: path does not exist", host)
                continue

            link_name = workspace / host.name
            if link_name.exists():
                continue

            link_name.symlink_to(host)

    def _is_allowed_mount(self, path: Path) -> bool:
        """Check if a path is under one of the allowed mount prefixes."""
        if not self._allowed_mount_prefixes:
            return True

        resolved = path.resolve()
        return any(
            resolved.is_relative_to(Path(prefix).resolve())
            for prefix in self._allowed_mount_prefixes
        )

    def _pick_flock_base_port(self, node_count: int) -> int:
        """Pick a free shared base port for a local flocked session.

        Local mini mode runs one Skuld broker plus ``node_count`` ravn peers.
        Skuld uses the session base directly, while the generated ravn flock is
        shifted by one mesh slot so its first persona does not collide with the
        primary broker.
        """
        for offset in range(DEFAULT_FLOCK_PORT_SCAN_LIMIT):
            base_port = DEFAULT_FLOCK_BASE_PORT + offset
            if base_port in self._allocated_flock_base_ports:
                continue
            required_ports: list[int] = [base_port, base_port + 1, base_port + 100]
            for index in range(node_count):
                ravn_index = index + 1
                required_ports.extend(
                    [
                        base_port + ravn_index * 2,
                        base_port + ravn_index * 2 + 1,
                        base_port + 100 + ravn_index,
                        base_port + 200 + ravn_index,
                    ]
                )

            if all(SdkPortAllocator._is_port_free(port) for port in required_ports):
                self._allocated_flock_base_ports.add(base_port)
                return base_port

        raise RuntimeError(
            "Could not find a free ravn flock base port "
            f"after scanning {DEFAULT_FLOCK_PORT_SCAN_LIMIT} candidates"
        )

    @staticmethod
    def _write_claude_md(_workspace: Path, _spec: SessionSpec) -> None:
        """No-op (deactivated in our pipeline).

        The system prompt AND the initial task are both delivered to the agent
        through the transport — the Claude SDK ``system_prompt`` option and
        ``--append-system-prompt`` for the subprocess transports, plus
        ``send_message`` for the initial prompt. The previous behavior wrote them
        into the workspace ``CLAUDE.md``, which CLOBBERED any real project
        CLAUDE.md and left a stale ``## Initial Task`` that bled into every later
        session — and any other Claude Code agent — that ran in the same folder.
        """
        return

    # ------------------------------------------------------------------
    # Process spawning & monitoring
    # ------------------------------------------------------------------

    @staticmethod
    def _flock_port_plan(base_port: int) -> FlockPortPlan:
        """Return the shared local mesh port layout for one flocked session."""
        return FlockPortPlan(
            session_base_port=base_port,
            ravn_base_port=base_port + 2,
            skuld_pub_port=base_port,
            skuld_rep_port=base_port + 1,
            skuld_handshake_port=base_port + 100,
        )

    async def _spawn_skuld(
        self,
        session: Session,
        spec: SessionSpec,
        workspace: Path,
        port: int,
        flock_plan: FlockPortPlan | None = None,
    ) -> int:
        """Spawn a Skuld broker subprocess that internally manages Claude.

        Returns:
            The PID of the spawned Skuld process.
        """
        session_id = str(session.id)

        # Resolve the command to run Skuld.
        # From the compiled binary: use the same binary with "platform skuld"
        # From source: use "python -m skuld"
        skuld_cmd = self._resolve_skuld_command()

        # Configure Skuld via env vars
        env = self._build_env(spec, workspace)

        # Inject pod_spec env vars from session contributors.
        flock_dir = workspace / ".flock"
        if spec.pod_spec and spec.pod_spec.env:
            for entry in spec.pod_spec.env:
                if name := entry.get("name"):
                    env[name] = entry.get("value", "")

            # Enable room mode so the web UI shows multi-participant view
            env["SKULD__ROOM__ENABLED"] = "true"

            # Configure Skuld's static discovery to find ravn flock peers.
            # The cluster.yaml is written by _start_flock after init.
            cluster_file = str(flock_dir / "cluster.yaml")
            env["SKULD__MESH__ADAPTERS"] = json.dumps(
                [
                    {
                        "adapter": "ravn.adapters.discovery.static.StaticDiscoveryAdapter",
                        "cluster_file": cluster_file,
                        "poll_interval_s": 5,
                    }
                ]
            )

        if flock_plan is not None:
            cleanup_skuld_mesh_sockets(flock_dir)
            skuld_pub, skuld_rep = skuld_mesh_addresses(flock_dir)
            env["SKULD__MESH__NNG__PUB_SUB_ADDRESS"] = skuld_pub
            env["SKULD__MESH__NNG__REQ_REP_ADDRESS"] = skuld_rep
            env["SKULD__MESH__HANDSHAKE_PORT"] = str(flock_plan.skuld_handshake_port)

        env["SKULD__SESSION__ID"] = session_id
        env["SKULD__SESSION__NAME"] = session.name
        # Session ownership — the broker enforces these on inbound WebSocket
        # connections (skuld.config.WsAuthConfig).
        if session.owner_id:
            env["SKULD__SESSION__OWNER_ID"] = session.owner_id
        if session.tenant_id:
            env["SKULD__SESSION__TENANT_ID"] = session.tenant_id
        model = str(session.model or spec.values.get("model", "") or "").strip()
        env["SKULD__SESSION__MODEL"] = model
        env["SKULD__SESSION__WORKSPACE_DIR"] = str(workspace)
        env["SKULD__HOST"] = "127.0.0.1"
        env["SKULD__PORT"] = str(port)
        env.setdefault("SKULD__TRANSPORT", "sdk")
        env["SKULD__PERSISTENCE_MOUNT_PATH"] = str(self._workspaces_dir)

        # Volundr API URL so Skuld can post chronicles/timeline events back
        server_host = self._server_host
        server_port = self._server_port
        env["SKULD__VOLUNDR_API_URL"] = f"http://{server_host}:{server_port}"

        session_vals = spec.values.get("session", {})
        system_prompt = session_vals.get("systemPrompt", "")
        initial_prompt = session_vals.get("initialPrompt", "")
        if system_prompt:
            env["SKULD__SESSION__SYSTEM_PROMPT"] = system_prompt
        if initial_prompt:
            env["SKULD__SESSION__INITIAL_PROMPT"] = initial_prompt

        # Claude transports still need the CLI binary location.
        if env.get("SKULD__CLI_TYPE", "claude") == "claude":
            env["SKULD__CLI_BINARY"] = self._resolve_claude_binary()

        log_path = workspace / ".skuld.log"
        log_file = log_path.open("w", encoding="utf-8")
        try:
            process = await asyncio.create_subprocess_exec(
                *skuld_cmd,
                stdout=log_file,
                stderr=log_file,
                cwd=str(workspace),
                env=env,
            )
        finally:
            log_file.close()

        logger.info(
            "Spawned Skuld process pid=%d port=%d session=%s",
            process.pid,
            port,
            session_id,
        )
        return process.pid

    async def _start_flock(
        self,
        session: Session,
        spec: SessionSpec,
        workspace: Path,
        flock_plan: FlockPortPlan,
        *,
        skuld_port: int,
    ) -> Path:
        """Init and start a ravn flock alongside the Skuld session.

        Extracts persona names from ``spec.pod_spec.extra_containers`` and
        uses ``ravn flock init/start`` to spawn daemon processes with static
        discovery.  Skuld is added to the cluster.yaml so ravn peers
        discover it on the mesh.
        """
        import subprocess as sp

        import yaml

        flock_dir = workspace / ".flock"
        personas = [
            c["name"].removeprefix("ravn-")
            for c in spec.pod_spec.extra_containers
            if c.get("name", "").startswith("ravn-")
        ]

        if not personas:
            return flock_dir

        await self._materialize_flock_personas(session, workspace, personas)

        # ravn flock init with static discovery (no mDNS). The ravn sidecars
        # start at index 1 because the primary Skuld broker occupies index 0.
        ravn_base_port = flock_plan.ravn_base_port
        sp.run(
            [
                sys.executable,
                "-m",
                "ravn",
                "flock",
                "init",
                *personas,
                "--flock-dir",
                str(flock_dir),
                "--discovery",
                "static",
                "--mesh-transport",
                "ipc",
                "--no-http-gateway",
                "--base-port",
                str(ravn_base_port),
                "--force",
            ],
            check=True,
            capture_output=True,
            cwd=str(workspace),
        )
        logger.info(
            "Flock init: personas=%s dir=%s session_base_port=%d ravn_base_port=%d",
            personas,
            flock_dir,
            flock_plan.session_base_port,
            ravn_base_port,
        )

        # Append Skuld as a peer in cluster.yaml so ravn nodes discover it
        cluster_path = flock_dir / "cluster.yaml"
        if cluster_path.exists():
            cluster = yaml.safe_load(cluster_path.read_text())
            skuld_pub, skuld_rep = skuld_mesh_addresses(flock_dir)
            skuld_peer_id = ""
            if spec.pod_spec and spec.pod_spec.env:
                for entry in spec.pod_spec.env:
                    name = entry.get("name", "")
                    value = entry.get("value", "")
                    if name == "SKULD__MESH__PEER_ID":
                        skuld_peer_id = value

            if skuld_peer_id and skuld_pub:
                cluster.setdefault("peers", []).append(
                    {
                        "peer_id": skuld_peer_id,
                        "persona": "coder",
                        "display_name": "skuld",
                        "pub_address": skuld_pub,
                        "rep_address": skuld_rep,
                    }
                )
                cluster_path.write_text(yaml.safe_dump(cluster, default_flow_style=False))
                logger.info("Added Skuld peer to cluster.yaml: %s", skuld_peer_id)

        workflow_cfg = spec.values.get("workflow")
        if isinstance(workflow_cfg, dict):
            for persona in personas:
                node_path = flock_dir / f"node-{persona}.yaml"
                if not node_path.exists():
                    continue
                node_config = yaml.safe_load(node_path.read_text()) or {}
                node_config["workflow"] = workflow_cfg
                node_path.write_text(yaml.safe_dump(node_config, default_flow_style=False))
            logger.info(
                "Injected workflow graph config into local flock node configs: %s",
                flock_dir,
            )

        mimir_runtime_cfg = _materialize_local_mimir_config(spec, flock_dir)
        if mimir_runtime_cfg is not None:
            for persona in personas:
                node_path = flock_dir / f"node-{persona}.yaml"
                if not node_path.exists():
                    continue
                node_config = yaml.safe_load(node_path.read_text()) or {}
                node_config["mimir"] = mimir_runtime_cfg
                node_path.write_text(
                    yaml.safe_dump(node_config, default_flow_style=False),
                    encoding="utf-8",
                )
            logger.info(
                "Injected Mimir runtime config into local flock node configs: %s",
                flock_dir,
            )

        self._apply_local_flock_overrides(
            spec,
            flock_dir,
            personas,
            workspace=workspace,
            skuld_port=skuld_port,
        )

        # ravn flock start
        sp.run(
            [
                sys.executable,
                "-m",
                "ravn",
                "flock",
                "start",
                "--flock-dir",
                str(flock_dir),
            ],
            check=True,
            capture_output=True,
            cwd=str(workspace),
        )
        logger.info("Flock started: %s", flock_dir)

        return flock_dir

    async def _materialize_flock_personas(
        self,
        session: Session,
        workspace: Path,
        personas: list[str],
    ) -> None:
        """Write owner-scoped persona YAML files into the workspace for flock startup.

        ``ravn flock init`` and the spawned ravn daemons only know how to resolve
        personas from the filesystem. Custom personas stored in Volundr's registry
        therefore need to be materialized into ``<workspace>/.ravn/personas`` so
        both init-time validation and runtime persona loading can find them.
        """
        registry = self._persona_registry
        owner_id = session.owner_id
        if registry is None or not owner_id or not personas:
            return

        get_persona_yaml = getattr(registry, "get_persona_yaml", None)
        if not callable(get_persona_yaml):
            return

        persona_dir = workspace / ".ravn" / "personas"
        written = 0
        for persona in personas:
            yaml_text = await get_persona_yaml(owner_id, persona)
            if not yaml_text:
                continue
            persona_dir.mkdir(parents=True, exist_ok=True)
            (persona_dir / f"{persona}.yaml").write_text(yaml_text, encoding="utf-8")
            written += 1

        if written:
            logger.info(
                "Materialized %d persona definition(s) for local flock in %s",
                written,
                persona_dir,
            )

    def _apply_local_flock_overrides(
        self,
        spec: SessionSpec,
        flock_dir: Path,
        personas: list[str],
        *,
        workspace: Path,
        skuld_port: int,
    ) -> None:
        """Apply workload-derived overrides to local flock node and cluster files."""
        import yaml

        flock_cfg = spec.values.get("flock")
        if not isinstance(flock_cfg, dict):
            return

        persona_entries = flock_cfg.get("personas")
        persona_overrides = {
            entry["name"]: entry
            for entry in persona_entries or []
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        }
        global_llm = flock_cfg.get("llm_config")
        if not isinstance(global_llm, dict):
            global_llm = None
        raw_daily_budget_usd = flock_cfg.get("daily_budget_usd")
        try:
            daily_budget_usd = float(raw_daily_budget_usd)
        except (TypeError, ValueError):
            daily_budget_usd = None
        global_max_tasks = flock_cfg.get("max_concurrent_tasks", DEFAULT_MAX_CONCURRENT)
        try:
            global_max_tasks = int(global_max_tasks)
        except (TypeError, ValueError):
            global_max_tasks = DEFAULT_MAX_CONCURRENT

        workflow_cfg = spec.values.get("workflow")
        if not isinstance(workflow_cfg, dict):
            workflow_cfg = None
        mcp_servers = _localize_mcp_servers(spec.values.get("mcpServers"), workspace)
        repo_workspace = workspace / "repo"
        workspace_root = repo_workspace if (repo_workspace / ".git").exists() else workspace

        try:
            from ravn.adapters.personas.loader import FilesystemPersonaAdapter

            persona_loader = FilesystemPersonaAdapter()
        except Exception:
            logger.warning("Failed to load persona adapter for local flock metadata", exc_info=True)
            persona_loader = None

        for persona in personas:
            node_path = flock_dir / f"node-{persona}.yaml"
            if not node_path.exists():
                continue

            node_config = yaml.safe_load(node_path.read_text()) or {}
            persona_override = persona_overrides.get(persona, {})

            effective_llm = merge_llm(
                defaults=None,
                global_override=global_llm,
                persona_override=persona_override.get("llm"),
            )
            if effective_llm:
                node_config["llm"] = effective_llm

            initiative_cfg = node_config.setdefault("initiative", {})
            initiative_cfg["max_concurrent_tasks"] = int(
                persona_override.get("max_concurrent_tasks") or global_max_tasks
            )
            if daily_budget_usd and daily_budget_usd > 0:
                budget_cfg = node_config.setdefault("budget", {})
                budget_cfg["daily_cap_usd"] = float(daily_budget_usd)

            permission_cfg = node_config.setdefault("permission", {})
            permission_cfg["workspace_root"] = str(workspace_root)

            skuld_cfg = node_config.setdefault("skuld", {})
            skuld_cfg["enabled"] = True
            skuld_cfg["broker_url"] = f"ws://127.0.0.1:{skuld_port}/ws/ravn"

            gateway_cfg = node_config.setdefault("gateway", {})
            platform_cfg = gateway_cfg.setdefault("platform", {})
            platform_cfg["enabled"] = True
            platform_cfg.setdefault("timeout", 30.0)
            platform_cfg.setdefault(
                "base_url",
                f"http://{_public_loopback_host(self._server_public_host)}:{self._server_port}",
            )

            persona_runtime_overrides: dict[str, Any] = {}
            system_prompt_extra = persona_override.get("system_prompt_extra")
            if isinstance(system_prompt_extra, str) and system_prompt_extra.strip():
                persona_runtime_overrides["system_prompt_extra"] = system_prompt_extra

            iteration_budget = persona_override.get("iteration_budget")
            if iteration_budget:
                initiative_cfg["iteration_budget"] = int(iteration_budget)
                persona_runtime_overrides["iteration_budget"] = int(iteration_budget)

            consumes_event_types = persona_override.get("consumes_event_types")
            if isinstance(consumes_event_types, list) and consumes_event_types:
                persona_runtime_overrides["consumes_event_types"] = [
                    str(event_type) for event_type in consumes_event_types
                ]

            executor_override = persona_override.get("executor")
            if isinstance(executor_override, dict) and executor_override:
                persona_runtime_overrides["executor"] = {
                    "adapter": str(executor_override.get("adapter") or ""),
                    "kwargs": (
                        dict(executor_override.get("kwargs") or {})
                        if isinstance(executor_override.get("kwargs"), dict)
                        else {}
                    ),
                }

            if persona_runtime_overrides:
                node_config["persona_overrides"] = persona_runtime_overrides
            else:
                node_config.pop("persona_overrides", None)

            if mcp_servers:
                node_config["mcp_servers"] = mcp_servers
            else:
                node_config.pop("mcp_servers", None)

            node_path.write_text(
                yaml.safe_dump(node_config, default_flow_style=False),
                encoding="utf-8",
            )

        cluster_path = flock_dir / "cluster.yaml"
        if not cluster_path.exists():
            return

        cluster = yaml.safe_load(cluster_path.read_text()) or {}
        peers = cluster.get("peers")
        if not isinstance(peers, list):
            return

        for peer in peers:
            if not isinstance(peer, dict):
                continue
            persona = peer.get("persona")
            if not isinstance(persona, str) or persona not in personas:
                continue

            tools: list[str] = []
            if persona_loader is not None:
                try:
                    persona_config = persona_loader.load(persona)
                except Exception:
                    logger.warning(
                        "Failed loading persona %s for local flock metadata",
                        persona,
                        exc_info=True,
                    )
                    persona_config = None
                if persona_config is not None:
                    tools = list(persona_config.allowed_tools or [])

            consumes, emits = _workflow_event_metadata(workflow_cfg, persona)
            if tools:
                peer["capabilities"] = tools
            if consumes:
                peer["consumes_event_types"] = consumes
            if emits:
                peer["emits_event_types"] = emits

        cluster_path.write_text(
            yaml.safe_dump(cluster, default_flow_style=False),
            encoding="utf-8",
        )
        logger.info("Applied local flock overrides and peer metadata: %s", flock_dir)

    def _stop_flock(self, flock_dir: str) -> None:
        """Stop a ravn flock via the CLI."""
        if not flock_dir:
            return
        import subprocess as sp

        flock_path = Path(flock_dir)
        try:
            sp.run(
                [
                    sys.executable,
                    "-m",
                    "ravn",
                    "flock",
                    "stop",
                    "--flock-dir",
                    flock_dir,
                ],
                capture_output=True,
                timeout=15,
            )
            cleanup_skuld_mesh_sockets(flock_path)
            logger.info("Flock stopped: %s", flock_dir)
        except Exception:
            logger.warning("Failed to stop flock at %s", flock_dir, exc_info=True)

    def _resolve_skuld_command(self) -> list[str]:
        """Resolve the command to run a Skuld subprocess.

        From the compiled binary: reuse the same binary with 'platform skuld'.
        From source: use 'python -m skuld'.
        """
        import sys

        # Check if we're running from a Nuitka-compiled binary.
        if getattr(sys, "frozen", False) or "__compiled__" in globals():
            return [sys.executable, "platform", "skuld"]

        # Running from source
        return [sys.executable, "-m", "skuld"]

    def _resolve_claude_binary(self) -> str:
        """Resolve the path to the claude binary."""
        if os.path.isabs(self._claude_binary):
            if os.path.isfile(self._claude_binary):
                return self._claude_binary
            raise FileNotFoundError(f"Claude binary not found: {self._claude_binary}")

        found = shutil.which(self._claude_binary)
        if found:
            return found

        raise FileNotFoundError(f"Claude binary '{self._claude_binary}' not found in PATH")

    @staticmethod
    def _build_env(spec: SessionSpec, workspace: Path) -> dict[str, str]:
        """Build environment variables for the Skuld process."""
        env = dict(os.environ)
        env["SKULD__SESSION__WORKSPACE_DIR"] = str(workspace)
        for key in (
            "SKULD__SKIP_PERMISSIONS",
            "SKULD__APPROVAL_POLICY",
            "SKULD__SANDBOX",
        ):
            env.pop(key, None)

        api_key = spec.values.get("anthropic_api_key", "")
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key

        git_token = spec.values.get("git_token", "")
        if git_token:
            env["GIT_TOKEN"] = git_token

        extra_env = spec.values.get("env", {})
        if isinstance(extra_env, dict):
            for key, value in extra_env.items():
                env[str(key)] = str(value)

        broker = spec.values.get("broker", {})
        if isinstance(broker, dict):
            cli_type = broker.get("cliType")
            if cli_type:
                env["SKULD__CLI_TYPE"] = str(cli_type)

            transport = broker.get("transport")
            if transport:
                env["SKULD__TRANSPORT"] = str(transport)

            transport_adapter = broker.get("transportAdapter")
            if transport_adapter:
                env["SKULD__TRANSPORT_ADAPTER"] = str(transport_adapter)

            resume_session_id = broker.get("resumeSessionId")
            if resume_session_id:
                env["SKULD__SESSION__RESUME_SESSION_ID"] = str(resume_session_id)

            if "skipPermissions" in broker:
                env["SKULD__SKIP_PERMISSIONS"] = str(bool(broker["skipPermissions"])).lower()

            approval_policy = broker.get("approvalPolicy", broker.get("approval_policy"))
            if approval_policy:
                env["SKULD__APPROVAL_POLICY"] = str(approval_policy)

            sandbox = broker.get("sandbox")
            if sandbox:
                env["SKULD__SANDBOX"] = str(sandbox)

            if "agentTeams" in broker:
                env["SKULD__AGENT_TEAMS"] = str(bool(broker["agentTeams"])).lower()

            telegram = broker.get("telegram")
            if isinstance(telegram, dict):
                if "enabled" in telegram:
                    env["SKULD__TELEGRAM__ENABLED"] = str(bool(telegram["enabled"])).lower()

                bot_token = telegram.get("botToken", telegram.get("bot_token"))
                if bot_token:
                    env["SKULD__TELEGRAM__BOT_TOKEN"] = str(bot_token)

                chat_id = telegram.get("chatId", telegram.get("chat_id"))
                if chat_id:
                    env["SKULD__TELEGRAM__CHAT_ID"] = str(chat_id)

                if "notifyOnly" in telegram or "notify_only" in telegram:
                    notify_only = telegram.get("notifyOnly", telegram.get("notify_only"))
                    env["SKULD__TELEGRAM__NOTIFY_ONLY"] = str(bool(notify_only)).lower()

                topic_mode = telegram.get("topicMode", telegram.get("topic_mode"))
                if topic_mode:
                    env["SKULD__TELEGRAM__TOPIC_MODE"] = str(topic_mode)

                message_thread_id = telegram.get(
                    "messageThreadId",
                    telegram.get("message_thread_id"),
                )
                if message_thread_id not in (None, ""):
                    env["SKULD__TELEGRAM__MESSAGE_THREAD_ID"] = str(message_thread_id)

        mcp_servers = _localize_mcp_servers(spec.values.get("mcpServers"), workspace)
        if mcp_servers:
            env["SKULD__MCP_SERVERS"] = json.dumps(mcp_servers)

        return env

    async def _monitor_process(self, session_id: str, pid: int) -> None:
        """Monitor a Claude process and update state on exit."""
        try:
            while True:
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                await asyncio.sleep(READY_POLL_INTERVAL)

            info = self._processes.get(session_id)
            if info and info.state == ProcessState.RUNNING:
                # Stop flock sidecars when Skuld exits
                if info.flock_dir:
                    self._stop_flock(info.flock_dir)
                info.state = ProcessState.STOPPED
                if info.port is not None:
                    self._port_allocator.release(info.port)
                if info.flock_base_port is not None:
                    self._allocated_flock_base_ports.discard(info.flock_base_port)
                self._persist_state()
                if self._skuld_registry is not None:
                    unregister = getattr(self._skuld_registry, "unregister", None)
                    if callable(unregister):
                        unregister(session_id)
                logger.info("Claude process exited pid=%d session=%s", pid, session_id)
                await self._notify_death(session_id)
        except asyncio.CancelledError:
            return

    async def _notify_death(self, session_id: str) -> None:
        """Best-effort notify the session service that a broker process died.

        Now that the process-state file reads STOPPED, the callback's pod-status
        reconcile flips the DB row to match. Failures here must never tear down
        the monitor task, so they are logged and swallowed.
        """
        if self._death_callback is None:
            return
        try:
            await self._death_callback(session_id)
        except Exception:
            logger.warning(
                "Death callback failed for session %s; periodic reconcile will catch it",
                session_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------

    async def _terminate_process(self, pid: int) -> None:
        """Send SIGTERM, wait for timeout, then SIGKILL if needed."""
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return

        for _ in range(self._stop_timeout * 2):
            await asyncio.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                return

        with suppress(OSError):
            os.kill(pid, signal.SIGKILL)
            logger.warning("Sent SIGKILL to pid=%d after timeout", pid)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _persist_state(self) -> None:
        """Write process state to JSON file."""
        self._state_file.parent.mkdir(parents=True, exist_ok=True)

        data = {sid: info.to_dict() for sid, info in self._processes.items()}

        tmp_path = self._state_file.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        tmp_path.replace(self._state_file)

    def _load_state(self) -> None:
        """Load state from JSON file, marking stale sessions as stopped."""
        if not self._state_file.exists():
            return

        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load state file: %s", exc)
            return

        for sid, info_data in data.items():
            info = ProcessInfo.from_dict(info_data)
            if info.state in (ProcessState.RUNNING, ProcessState.STARTING):
                if self._is_recoverable_local_process(info):
                    info.state = ProcessState.RUNNING
                else:
                    info.state = ProcessState.STOPPED
                    logger.info(
                        "Marked stale session %s as stopped (process dead)",
                        sid,
                    )
            self._processes[sid] = info

        self._persist_state()

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        """Check if a process is still running."""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _process_command(pid: int) -> str:
        """Best-effort command line lookup for a running PID."""
        proc_cmdline = Path(f"/proc/{pid}/cmdline")
        with suppress(OSError):
            if proc_cmdline.exists():
                raw = proc_cmdline.read_bytes()
                if raw:
                    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _is_recoverable_local_process(self, info: ProcessInfo) -> bool:
        """Return True when a saved PID still looks like a locally managed Skuld process."""
        if info.pid is None or not self._is_process_alive(info.pid):
            return False
        cmdline = self._process_command(info.pid)
        if not cmdline:
            return False
        normalized = cmdline.lower()
        return (
            " -m skuld" in normalized
            or normalized.endswith(" skuld")
            or " platform skuld" in normalized
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _active_sessions(self) -> list[str]:
        """Return session IDs with active (running/starting) processes."""
        return [
            sid
            for sid, info in self._processes.items()
            if info.state in (ProcessState.RUNNING, ProcessState.STARTING)
        ]

    def _reconcile_active(self) -> list[str]:
        """Active session IDs, after reaping entries whose process is dead.

        ``_monitor_process`` only reaps a session when its broker exits while the
        monitor task is alive. Brokers orphaned by a hard kill (or a dead monitor)
        keep their RUNNING/STARTING slot forever, which eventually trips the
        concurrent-session cap with *phantom* sessions ("Max concurrent reached"
        with nothing really running). Verify pid liveness here so the cap reflects
        real in-flight work. A STARTING entry with no pid yet is mid-spawn — left
        alone.
        """
        reaped: list[str] = []
        for sid, info in self._processes.items():
            if info.state not in (ProcessState.RUNNING, ProcessState.STARTING):
                continue
            if info.pid is not None and not self._is_process_alive(info.pid):
                info.state = ProcessState.STOPPED
                if info.port is not None:
                    self._port_allocator.release(info.port)
                if info.flock_base_port is not None:
                    self._allocated_flock_base_ports.discard(info.flock_base_port)
                reaped.append(sid)
        if reaped:
            self._persist_state()
            logger.warning(
                "Reaped %d phantom session(s) holding concurrent slots (process dead): %s",
                len(reaped),
                ", ".join(s[:8] for s in reaped),
            )
        return self._active_sessions()
