"""Local process adapter for pod management.

Runs Claude Code as a local subprocess instead of Kubernetes pods.
Implements PodManager so Volundr's SessionService, REST endpoints,
and event pipeline work unchanged — only the pod manager swaps from
K8s to local process.

Constructor accepts plain kwargs (dynamic adapter pattern):
    adapter: "volundr.adapters.outbound.local_process.LocalProcessPodManager"
    workspaces_dir: "~/.niuu/workspaces"
    claude_binary: "claude"
    max_concurrent: (unset: no limit)
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

import httpx

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.mesh_settings import (
    mesh_environment,
    mesh_with_defaults,
    parse_mesh_settings,
)
from volundr.adapters.outbound.vm_hosted_unit import (
    HostedMemberLaunch,
    HostedUnitError,
)
from volundr.domain.models import HOSTED_MEMBERS_KEY as UNIT_MEMBERS_KEY
from volundr.domain.models import (
    GitSource,
    LocalMountSource,
    Session,
    SessionSpec,
    SessionStatus,
)
from volundr.domain.ports import (
    HostedMemberSource,
    PodManager,
    PodStartResult,
    SessionCapacity,
)
from volundr.domain.services.forge_session_launch import launch_id_of
from volundr.domain.services.session import SessionCapacityError
from volundr.domain.workflow_session import workflow_session_environment

logger = logging.getLogger(__name__)

# Default configuration values
DEFAULT_WORKSPACES_DIR = "~/.niuu/workspaces"
# Broker-owned present-file staging for local sessions, under the workspaces dir.
LOCAL_PRESENTED_FILES_DIR = ".forge-presented"

DEFAULT_CLAUDE_BINARY = "claude"
DEFAULT_SDK_PORT_START = 9100
DEFAULT_STOP_TIMEOUT = 10
DEFAULT_STATE_FILE = "~/.niuu/forge-state.json"
DEFAULT_ALLOWED_MOUNT_PREFIXES: list[str] = []

# Poll interval for wait_for_ready
READY_POLL_INTERVAL = 0.5

# A workflow run's unit keeps its mesh registry under the mesh directory, one directory per
# root, so the Skulds of one run find each other and no other session's.
UNIT_MESH_DIRECTORY = "units"
UNIT_MESH_REGISTRY_FILE = "peers.json"


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
    managed_by: str = "local_process"
    #: The launch (``forge_mcp`` launch id) this process was started under.
    launch_id: str | None = None
    #: A unit root: the process group it leads, and the group its members were started in.
    group: int | None = None
    #: A unit member: the id of the unit root whose group it runs in.
    hosted_by: str | None = None

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
            managed_by=data.get("managed_by", "local_process"),
            launch_id=data.get("launch_id"),
            group=data.get("group"),
            hosted_by=data.get("hosted_by"),
        )


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
        """Check if a port is free to bind on loopback.

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


#: Broker settings carrying the session credential (skuld ``forge_mcp.token`` /
#: ``forge_mcp.grants``). Skuld scrubs the token from every agent process it spawns.
FORGE_MCP_TOKEN_ENV = "SKULD__FORGE_MCP__TOKEN"
FORGE_MCP_GRANTS_ENV = "SKULD__FORGE_MCP__GRANTS"
#: The broker setting naming a file that holds the credential (``forge_mcp.token_file``).
FORGE_MCP_TOKEN_FILE_ENV = "SKULD__FORGE_MCP__TOKEN_FILE"


def _apply_forge_session_env(env: dict[str, str], spec: SessionSpec) -> None:
    """Put this launch's session credential (and only it) into the broker env.

    The token travels only in the broker's process environment: never argv, the
    state file or a log line.
    """
    env.pop(FORGE_MCP_TOKEN_ENV, None)
    env.pop(FORGE_MCP_GRANTS_ENV, None)
    credential = spec.forge_session
    if credential is None:
        return
    env[FORGE_MCP_TOKEN_ENV] = credential.token
    env[FORGE_MCP_GRANTS_ENV] = ",".join(grant.value for grant in credential.grants)


class LocalProcessPodManager(PodManager):
    """Manages Claude Code as local subprocesses.

    Implements PodManager so Volundr's existing SessionService, REST
    endpoints, and event pipeline work unchanged.
    """

    @property
    def runtime_backend(self) -> str:
        return "process"

    @property
    def delivers_forge_session_token(self) -> bool:
        """The broker receives its session credential in its own process env."""
        return True

    #: Takes Skuld's ``mesh`` settings and hands them to every session (``mesh_settings``).
    carries_mesh_setting = True

    def __init__(
        self,
        *,
        workspaces_dir: str = DEFAULT_WORKSPACES_DIR,
        claude_binary: str = DEFAULT_CLAUDE_BINARY,
        max_concurrent: int | None = None,
        sdk_port_start: int = DEFAULT_SDK_PORT_START,
        stop_timeout: int = DEFAULT_STOP_TIMEOUT,
        state_file: str = DEFAULT_STATE_FILE,
        allowed_mount_prefixes: list[str] | None = None,
        server_public_host: str = "127.0.0.1",
        server_host: str = "127.0.0.1",
        server_port: int = 8080,
        preview_router_auth: dict | None = None,
        mesh: dict | str | None = None,
        **_extra: object,
    ):
        """``preview_router_auth`` is the Skuld ``preview.router_auth`` adapter
        (``{"adapter": ..., "kwargs": {...}}``) for a host with an identity
        provider; on a host without one, local sessions use
        ``skuld.preview_adapters.NoRouterAuth``. Unset on an authenticated host,
        local sessions run without live preview."""
        self._workspaces_dir = Path(str(workspaces_dir)).expanduser()
        self._preview_router_auth = dict(preview_router_auth or {})
        # What the operator set; a run's unit defaults its own registry over this.
        self._operator_mesh = parse_mesh_settings(mesh)
        # Sessions on one machine find each other through one registry file beside the
        # workspaces; the operator only has to enable the mesh.
        self._mesh = mesh_with_defaults(
            self._operator_mesh,
            {
                "discovery_adapters": [
                    {
                        "adapter": "registry",
                        "path": str(self._workspaces_dir.parent / "mesh" / "peers.json"),
                    }
                ]
            },
        )
        self._claude_binary = str(claude_binary)
        # No limit unless the operator sets one: the machine's memory and the model
        # subscriptions' own concurrency are the real bounds, and they refuse on their own.
        self._max_concurrent = None if max_concurrent is None else int(max_concurrent)
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
        self._processes: dict[str, ProcessInfo] = {}
        self._monitors: dict[str, asyncio.Task] = {}
        if not hasattr(self, "_ready"):
            self._ready: set[str] = set()
        self._broker_startup_failures: dict[str, str] = {}
        self._skuld_registry: object | None = None  # Set via set_skuld_registry()
        # Notified (best-effort) when a managed broker process exits, so the
        # SessionService can reconcile the DB row promptly instead of waiting for
        # the periodic sweep. Set via set_death_callback().
        self._death_callback: Callable[[str], Awaitable[None]] | None = None
        # Resolves a unit root's members (their sessions and their own specs); see
        # configure_hosted_members.
        self._hosted_members: HostedMemberSource | None = None

        self._load_state()

    def _preview_env(self, session_id: str) -> dict[str, str]:
        """Skuld live-preview settings for a session on this shared host.

        Only the agent's own processes count as the session's (a laptop listens
        on plenty of other ports): Skuld's descendants, and processes carrying
        this session's SKULD__SESSION__ID, which the agent inherits. The router
        credential check follows the host: none where it has no identity provider.
        """
        if getattr(self._skuld_registry, "dev_identity", False):
            router_auth: dict = {"adapter": "skuld.preview_adapters.NoRouterAuth"}
        elif self._preview_router_auth:
            router_auth = self._preview_router_auth
        else:
            logger.info(
                "Live preview is off for local sessions: this host authenticates "
                "users, so set the pod manager's preview_router_auth "
                "(skuld.preview_adapters.WorkloadJwtRouterAuth) to turn it on"
            )
            return {"SKULD__PREVIEW__ENABLED": "false"}
        return {
            "SKULD__PREVIEW__ENABLED": "true",
            "SKULD__PREVIEW__PORT_SCANNER": json.dumps(
                {
                    "adapter": "skuld.preview_adapters.LsofSessionPortScanner",
                    "kwargs": {"session_id": session_id},
                }
            ),
            "SKULD__PREVIEW__ROUTER_AUTH": json.dumps(router_auth),
        }

    def set_skuld_registry(self, registry: object) -> None:
        """Inject the SkuldPortRegistry for proxy routing."""
        self._skuld_registry = registry
        register = getattr(registry, "register", None)
        if not callable(register):
            return
        for session_id, info in self._processes.items():
            if info.state == ProcessState.RUNNING and info.port is not None:
                register(session_id, info.port)

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
        if session.hosted_by is not None:
            raise HostedUnitError(
                f"Session {session.id} is a member of unit {session.hosted_by}: a member "
                "starts with its unit, never on its own"
            )
        if self._unit_member_ids(session):
            return await self._start_unit(session, spec)

        active = self._reconcile_active()
        if self._max_concurrent is not None and len(active) >= self._max_concurrent:
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
            raise SessionCapacityError(self._capacity_snapshot(active))
        logger.info(
            "Provisioning session %s (%d concurrent sessions, limit %s)",
            session_id[:8],
            len(active),
            self._max_concurrent,
        )

        workspace = await self._provision_workspace(session, spec)
        port = self._port_allocator.allocate()

        info = ProcessInfo(
            session_id=session_id,
            port=port,
            workspace=str(workspace),
            state=ProcessState.STARTING,
        )
        self._ready.discard(session_id)
        self._broker_startup_failures.pop(session_id, None)
        self._processes[session_id] = info
        self._persist_state()

        try:
            pid = await self._spawn_skuld(session, spec, workspace, port)
            info.pid = pid
            info.state = ProcessState.RUNNING
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
            if info.pid is not None:
                await self._terminate_process(info.pid)
            info.state = ProcessState.FAILED
            self._port_allocator.release(port)
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

        if info.group is not None:
            return await self._stop_unit(session_id, info)

        monitor = self._monitors.pop(session_id, None)
        if monitor and not monitor.done():
            monitor.cancel()

        if info.pid is not None:
            await self._terminate_process(info.pid)

        if info.port is not None:
            self._port_allocator.release(info.port)

        if self._skuld_registry is not None:
            self._skuld_registry.unregister(session_id)

        info.state = ProcessState.STOPPED
        self._ready.discard(session_id)
        self._broker_startup_failures.pop(session_id, None)
        self._persist_state()
        return True

    async def status(self, session: Session) -> SessionStatus:
        """Get the current status of the local Claude process."""
        session_id = str(session.id)
        info = self._processes.get(session_id)

        if info is None:
            return SessionStatus.STOPPED

        if session.hosted_by is not None and await self._restart_member_if_new_launch(
            session, info
        ):
            return SessionStatus.PROVISIONING

        match info.state:
            case ProcessState.STARTING:
                return SessionStatus.PROVISIONING
            case ProcessState.RUNNING:
                if session_id in self._ready:
                    return SessionStatus.RUNNING
                # Readiness lives in memory only. A process adopted from the state
                # file after an API restart must be re-observed, or a healthy
                # session is demoted to provisioning and never promoted again.
                if await self._broker_healthy(session_id):
                    self._ready.add(session_id)
                    return SessionStatus.RUNNING
                return SessionStatus.PROVISIONING
            case ProcessState.STOPPED:
                return SessionStatus.STOPPED
            case ProcessState.FAILED:
                return SessionStatus.FAILED
        raise AssertionError("Unreachable status fallthrough")

    async def status_detail(self, session: Session) -> str | None:
        """Return the broker's durable startup failure, when one was reported."""
        info = self._processes.get(str(session.id))
        return info.error if info is not None else None

    async def wait_for_ready(self, session: Session, timeout: float) -> SessionStatus:
        """Wait until the Claude process is running or fails."""
        session_id = str(session.id)
        elapsed = 0.0

        while elapsed < timeout:
            info = self._processes.get(session_id)

            if info is None:
                return SessionStatus.FAILED

            if info.state == ProcessState.RUNNING:
                if await self._broker_healthy(session_id):
                    self._ready.add(session_id)
                    return SessionStatus.RUNNING
                if session_id in self._broker_startup_failures:
                    await self._fail_starting_runtime(session_id, info)
                    return SessionStatus.FAILED

            if info.state == ProcessState.FAILED:
                return SessionStatus.FAILED

            if info.state == ProcessState.STOPPED:
                return SessionStatus.STOPPED

            await asyncio.sleep(READY_POLL_INTERVAL)
            elapsed += READY_POLL_INTERVAL

        return SessionStatus.FAILED

    def _broker_ready_url(self, session_id: str) -> str | None:
        info = self._processes.get(session_id)
        if info is None or info.port is None:
            return None
        return f"http://127.0.0.1:{info.port}/ready"

    async def _broker_healthy(self, session_id: str) -> bool:
        """Probe broker readiness and remember a terminal startup failure."""
        url = self._broker_ready_url(session_id)
        if url is None:
            return False
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url)
        except httpx.HTTPError:
            return False
        try:
            payload = response.json()
        except (AttributeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if response.status_code == 200 and payload.get("ready") is True:
            self._broker_startup_failures.pop(session_id, None)
            return True
        if payload.get("startup_state") == "failed":
            self._broker_startup_failures[session_id] = str(
                payload.get("error") or "broker startup failed"
            )
        return False

    async def _fail_starting_runtime(self, session_id: str, info: ProcessInfo) -> None:
        """Tear down a broker that reported a terminal startup failure."""
        monitor = self._monitors.pop(session_id, None)
        if monitor is not None and monitor is not asyncio.current_task() and not monitor.done():
            monitor.cancel()
        if info.pid is not None:
            await self._terminate_process(info.pid)
        if info.port is not None:
            self._port_allocator.release(info.port)
        if self._skuld_registry is not None:
            unregister = getattr(self._skuld_registry, "unregister", None)
            if callable(unregister):
                unregister(session_id)
        self._ready.discard(session_id)
        info.error = self._broker_startup_failures.get(session_id)
        info.state = ProcessState.FAILED
        self._persist_state()

    # ------------------------------------------------------------------
    # A workflow run's unit: the root and every member, one process group
    # ------------------------------------------------------------------
    #
    # On a laptop a run is one local process group (docs/design/workflow-mesh-handoff.md,
    # section 2): the root's Skuld leads the group and every member's Skuld joins it, so
    # they start and stop together and nothing outside the group is touched. Each member
    # stays its own Forge session with its own Skuld process, port, credential (in that
    # process's environment only) and ``ProcessInfo``; the checkout is shared and the mesh
    # is a registry file in a directory only this run's Skulds are given.

    @staticmethod
    def _unit_member_ids(session: Session) -> list[str]:
        """The members a unit root was started with; empty for any other session."""
        members = session.workload_config.get(UNIT_MEMBERS_KEY) or []
        return [str(member["id"]) for member in members]

    def _unit_registry(self, root_id: str) -> Path:
        """The registry file the Skulds of one run find each other through."""
        return (
            self._workspaces_dir.parent
            / "mesh"
            / UNIT_MESH_DIRECTORY
            / root_id
            / UNIT_MESH_REGISTRY_FILE
        )

    def _unit_mesh(self, root_id: str) -> dict:
        """The mesh of a run's unit: nng on loopback, discovered through the unit's registry.

        Whatever the operator set stays as set; only the discovery default changes from the
        machine-wide registry to this run's. A run needs the mesh, so it does not start on a
        host whose mesh is off.
        """
        enabled = self._operator_mesh.get("enabled")
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() == "true"
        if not enabled:
            raise HostedUnitError(
                f"Unit {root_id} hosts a workflow run, whose members hand steps to each other "
                "over the mesh, but this host's mesh is off: set `mesh.enabled: true` in "
                "config.yaml (the pod manager's `mesh`) and restart"
            )
        return mesh_with_defaults(
            self._operator_mesh,
            {
                "discovery_adapters": [
                    {"adapter": "registry", "path": str(self._unit_registry(root_id))}
                ]
            },
        )

    def initial_chat_endpoint(self, session: Session) -> str | None:
        """A member's chat endpoint is Forge's own proxy route for the member, not its unit's."""
        if session.hosted_by is None:
            return None
        server_host = _public_loopback_host(self._server_public_host)
        return f"ws://{server_host}:{self._server_port}/s/{session.id}/session"

    async def broker_target(self, session: Session) -> SessionProxyTarget | None:
        """Where Forge reaches a member's broker: its own loopback port.

        A member's endpoint is built from its unit's address, which names the root's route;
        Forge's server-side calls to the member go to the member's own Skuld instead.
        """
        if session.hosted_by is None:
            return None
        info = self._processes.get(str(session.id))
        if (
            info is None
            or info.port is None
            or info.state
            not in (
                ProcessState.RUNNING,
                ProcessState.STARTING,
            )
        ):
            raise ValueError(f"Member session {session.id} has no running Skuld on this host")
        return SessionProxyTarget(
            service_url=f"http://127.0.0.1:{info.port}",
            connect_host="127.0.0.1",
            connect_port=info.port,
        )

    async def _resolve_members(self, root: Session) -> list[HostedMemberLaunch]:
        """The unit's members as the source resolves them; they must be the recorded ones."""
        if self._hosted_members is None:
            raise HostedUnitError(
                f"Session {root.id} is a unit that hosts {len(self._unit_member_ids(root))} "
                "member session(s), but this local pod manager has no member source; compose "
                "it with configure_hosted_members"
            )
        members = list(await self._hosted_members(root.id, None))
        resolved = sorted(str(member.session.id) for member in members)
        if resolved != sorted(self._unit_member_ids(root)):
            raise HostedUnitError(
                f"The members resolved for unit {root.id} ({resolved}) differ from the members "
                f"it was started with ({sorted(self._unit_member_ids(root))}): a run's members "
                "are fixed when its unit starts"
            )
        for member in members:
            if member.session.hosted_by != root.id:
                raise HostedUnitError(
                    f"Session {member.session.id} was resolved for unit {root.id} but is "
                    f"hosted by {member.session.hosted_by}"
                )
        return members

    @staticmethod
    def _source_key(session: Session) -> tuple | None:
        """What a session's workspace source names, for the run's one-checkout check."""
        source = session.source
        if isinstance(source, GitSource) and source.repo:
            return ("git", source.repo, source.branch, source.base_branch)
        if isinstance(source, LocalMountSource) and source.local_path:
            return ("local", str(Path(source.local_path).expanduser()))
        if isinstance(source, LocalMountSource) and source.paths:
            return ("mounts", tuple(sorted(m.host_path for m in source.paths)))
        return None

    async def _provision_unit_workspace(
        self, root: Session, spec: SessionSpec, members: list[HostedMemberLaunch]
    ) -> Path:
        """The one checkout of the run, shared by the root and every member.

        The root's own source wins when it names one; otherwise the members' must agree. A
        member that names another repository or branch is refused: it would otherwise work
        in a checkout of something it did not ask for.
        """
        wanted = {
            session.id: self._source_key(session) for session in (root, *self._sessions(members))
        }
        distinct = {key for key in wanted.values() if key is not None}
        if len(distinct) > 1:
            raise HostedUnitError(
                f"The sessions of unit {root.id} name different workspaces "
                f"{sorted(map(str, distinct))}: a run's members share one checkout of one "
                "repository and branch"
            )
        if wanted[root.id] is None:
            for member in members:
                if wanted[member.session.id] is not None:
                    # The run's checkout lives under the root, cloned as the member asked.
                    sourced = root.model_copy(update={"source": member.session.source})
                    return await self._provision_workspace(sourced, member.spec)
        return await self._provision_workspace(root, spec)

    @staticmethod
    def _sessions(members: list[HostedMemberLaunch]) -> list[Session]:
        return [member.session for member in members]

    async def _start_unit(self, root: Session, spec: SessionSpec) -> PodStartResult:
        """Start the root and every member as one process group; nothing starts half."""
        root_id = str(root.id)
        members = await self._resolve_members(root)
        mesh = self._unit_mesh(root_id)

        active = self._reconcile_active()
        needed = 1 + len(members)
        if self._max_concurrent is not None and len(active) + needed > self._max_concurrent:
            logger.warning(
                "Max concurrent reached: %d/%d slots in use, unit %s needs %d",
                len(active),
                self._max_concurrent,
                root_id[:8],
                needed,
            )
            raise SessionCapacityError(
                SessionCapacity(
                    limit=self._max_concurrent,
                    active=len(active),
                    remedy=(
                        f"{self._capacity_remedy()} (this run's unit needs {needed} free "
                        f"slots: the root and {len(members)} member(s); "
                        f"{self._max_concurrent - len(active)} are free)"
                    ),
                )
            )
        logger.info(
            "Provisioning unit %s: the root and %d member(s) (%d concurrent sessions, limit %s)",
            root_id[:8],
            len(members),
            len(active),
            self._max_concurrent,
        )

        workspace = await self._provision_unit_workspace(root, spec, members)
        registry = self._unit_registry(root_id)
        shutil.rmtree(registry.parent, ignore_errors=True)  # a fresh unit never inherits peers
        registry.parent.mkdir(parents=True, exist_ok=True)

        launches = [(root, spec, None), *((m.session, m.spec, root_id) for m in members)]
        infos: list[ProcessInfo] = []
        group: int | None = None
        try:
            for session, session_spec, hosted_by in launches:
                session_id = str(session.id)
                self._ready.discard(session_id)
                self._broker_startup_failures.pop(session_id, None)
                info = ProcessInfo(
                    session_id=session_id,
                    port=self._port_allocator.allocate(),
                    workspace=str(workspace),
                    state=ProcessState.STARTING,
                    launch_id=launch_id_of(session.workload_config),
                    hosted_by=hosted_by,
                )
                self._processes[session_id] = info
                infos.append(info)
                self._persist_state()
                pid = await self._spawn_skuld(
                    session,
                    session_spec,
                    workspace,
                    info.port,
                    mesh=mesh,
                    process_group=0 if group is None else group,
                )
                info.pid = pid
                if group is None:
                    group = pid
                    info.group = group
                info.state = ProcessState.RUNNING
                self._persist_state()
        except Exception:
            await self._abort_unit(infos, group)
            raise

        for info in infos:
            self._after_spawn(info)

        server_host = _public_loopback_host(self._server_public_host)
        return PodStartResult(
            chat_endpoint=f"ws://{server_host}:{self._server_port}/s/{root_id}/session",
            code_endpoint=f"file://{workspace}",
            pod_name=f"local-{root_id[:8]}",
        )

    def _after_spawn(self, info: ProcessInfo) -> None:
        """Route to a spawned Skuld through the proxy, and watch it."""
        if self._skuld_registry is not None and info.port is not None:
            self._skuld_registry.register(info.session_id, info.port)
        self._monitors[info.session_id] = asyncio.create_task(
            self._monitor_process(info.session_id, info.pid),
            name=f"monitor-{info.session_id}",
        )

    async def _abort_unit(self, infos: list[ProcessInfo], group: int | None) -> None:
        """A unit that cannot start whole does not start: its group is ended and marked failed."""
        if group is not None:
            await self._terminate_group(group)
        for info in infos:
            if info.port is not None:
                self._port_allocator.release(info.port)
            info.state = ProcessState.FAILED
        self._persist_state()

    async def _stop_unit(self, root_id: str, root: ProcessInfo) -> bool:
        """Stop the root and every member together: their group ends, nothing else does."""
        members = [i for i in self._processes.values() if i.hosted_by == root_id]
        for info in (root, *members):
            monitor = self._monitors.pop(info.session_id, None)
            if monitor and not monitor.done():
                monitor.cancel()
        if root.group is not None:
            await self._terminate_group(root.group)
        for info in (root, *members):
            self._mark_stopped(info)
        shutil.rmtree(self._unit_registry(root_id).parent, ignore_errors=True)
        self._persist_state()
        return True

    def _mark_stopped(self, info: ProcessInfo) -> None:
        if info.state not in (ProcessState.RUNNING, ProcessState.STARTING):
            return
        if info.port is not None:
            self._port_allocator.release(info.port)
        if self._skuld_registry is not None:
            unregister = getattr(self._skuld_registry, "unregister", None)
            if callable(unregister):
                unregister(info.session_id)
        info.state = ProcessState.STOPPED
        self._ready.discard(info.session_id)
        self._broker_startup_failures.pop(info.session_id, None)

    async def _end_unit_after_root(self, root_id: str, root: ProcessInfo) -> list[str]:
        """The root's Skuld exited: the unit is over, so its members' group ends with it.

        A member cannot do a run's work without the root's room and mesh, and a run is one
        unit, so the members are not left running. Returns the members that were running,
        for the caller to report.
        """
        if root.group is None:
            return []
        members = [
            i
            for i in self._processes.values()
            if i.hosted_by == root_id and i.state in (ProcessState.RUNNING, ProcessState.STARTING)
        ]
        for info in members:
            monitor = self._monitors.pop(info.session_id, None)
            if monitor and not monitor.done() and monitor is not asyncio.current_task():
                monitor.cancel()
        await self._terminate_group(root.group)
        for info in members:
            self._mark_stopped(info)
        self._persist_state()
        logger.warning(
            "Unit root %s exited: stopped its %d running member(s)", root_id[:8], len(members)
        )
        return [info.session_id for info in members]

    async def _terminate_group(self, group: int) -> None:
        """SIGTERM a process group, wait for it to empty, then SIGKILL what is left."""
        try:
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            return
        for _ in range(self._stop_timeout * 2):
            await asyncio.sleep(0.5)
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return
        with suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)
            logger.warning("Sent SIGKILL to process group %d after timeout", group)

    async def restart_hosted_member(self, member: Session) -> None:
        """Start a member's Skuld again in its unit's process group, under its new launch."""
        info = self._processes.get(str(member.id))
        if info is None:
            raise HostedUnitError(
                f"Member session {member.id} has no process on this host, so it cannot be "
                "started again; stop the run and start it again"
            )
        if not await self._restart_member_if_new_launch(member, info):
            raise HostedUnitError(
                f"Member session {member.id} was not restarted: its unit {member.hosted_by} is "
                "not running on this host, or the process already runs under its current launch"
            )

    async def _restart_member_if_new_launch(self, member: Session, info: ProcessInfo) -> bool:
        """Start a member's Skuld again, in place, when Forge recorded a new launch for it.

        Forge records a new launch (and a new credential) when a member is started again
        inside its running unit; its process has to be replaced, in the unit's group, by one
        started under that credential. Returns True when it restarted the member.
        """
        launch = launch_id_of(member.workload_config)
        if launch is None or info.launch_id == launch:
            return False
        root_id = str(member.hosted_by)
        root = self._processes.get(root_id)
        if root is None or root.group is None or root.state is not ProcessState.RUNNING:
            return False
        if self._hosted_members is None:
            raise HostedUnitError(
                f"Member session {member.id} was started again in unit {root_id}, but this "
                "local pod manager has no member source; compose it with "
                "configure_hosted_members"
            )
        launches = list(await self._hosted_members(member.hosted_by, member.id))
        if [str(m.session.id) for m in launches] != [str(member.id)]:
            raise HostedUnitError(
                f"The member source answered {[str(m.session.id) for m in launches]} for "
                f"member {member.id}"
            )
        member_id = str(member.id)
        monitor = self._monitors.pop(member_id, None)
        if monitor and not monitor.done():
            monitor.cancel()
        if info.pid is not None:
            await self._terminate_process(info.pid)
        self._mark_stopped(info)
        info.port = self._port_allocator.allocate()
        info.state = ProcessState.STARTING
        info.error = None
        info.launch_id = launch_id_of(launches[0].session.workload_config)
        self._persist_state()
        try:
            info.pid = await self._spawn_skuld(
                launches[0].session,
                launches[0].spec,
                Path(info.workspace),
                info.port,
                mesh=self._unit_mesh(root_id),
                process_group=root.group,
            )
        except Exception:
            self._port_allocator.release(info.port)
            info.state = ProcessState.FAILED
            self._persist_state()
            raise
        info.state = ProcessState.RUNNING
        self._persist_state()
        self._after_spawn(info)
        return True

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
        """Clone on first start; preserve the existing checkout on restart."""
        repo_dir = workspace / "repo"
        if (repo_dir / ".git").exists():
            code, _, _ = await self._run_git(
                repo_dir, "--git-dir=.git", "rev-parse", "--verify", "HEAD^{commit}"
            )
            if code != 0:
                raise RuntimeError(
                    f"Existing git checkout at {repo_dir} has no valid HEAD. "
                    "Repair it or move it aside before restarting; existing files were preserved."
                )
            logger.info("Reusing existing git checkout at %s", repo_dir)
            return

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
            str(repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode(errors="replace")
            error_msg = re.sub(r"://[^@]+@", "://***@", error_msg)
            raise RuntimeError(f"Git clone failed: {error_msg}")

        started_from = ""
        if branch:
            checkout_ok = await self._checkout_tracking_branch(repo_dir, branch)
            if not checkout_ok:
                fallback_branch = base_branch or await self._remote_head_branch(repo_dir)
                if fallback_branch:
                    fallback_ok = await self._checkout_tracking_branch(repo_dir, fallback_branch)
                    if fallback_ok:
                        await self._create_branch(repo_dir, branch)
                        started_from = fallback_branch
        # Record where this branch started, so the session's changes can be shown against it.
        started_from = started_from or base_branch or await self._remote_head_branch(repo_dir)
        if started_from:
            code, _, stderr = await self._run_git(
                repo_dir, "config", "niuu.baseBranch", started_from
            )
            if code != 0:
                raise RuntimeError(f"Could not record the workspace's base branch: {stderr}")

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

    async def _spawn_skuld(
        self,
        session: Session,
        spec: SessionSpec,
        workspace: Path,
        port: int,
        *,
        mesh: dict | None = None,
        process_group: int | None = None,
    ) -> int:
        """Spawn a Skuld broker subprocess that internally manages Claude.

        ``mesh`` replaces the manager's mesh settings for this process (a run's unit has its
        own registry), and ``process_group`` places it in a group: 0 starts a new group it
        leads, a pid joins that process's group.

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
        if spec.pod_spec and spec.pod_spec.env:
            for entry in spec.pod_spec.env:
                if name := entry.get("name"):
                    env[name] = entry.get("value", "")

        env["SKULD__SESSION__ID"] = session_id
        env["SKULD__SESSION__NAME"] = session.name
        # Session ownership — the broker enforces these on inbound WebSocket
        # connections (skuld.config.WsAuthConfig).
        if session.owner_id:
            env["SKULD__SESSION__OWNER_ID"] = session.owner_id
        if session.tenant_id:
            env["SKULD__SESSION__TENANT_ID"] = session.tenant_id
        # This pod is reached exclusively through niuu.session_proxy on the
        # process backend (never a Kubernetes-style Gateway/ext_authz
        # boundary), so it is the one backend where the proxy's own
        # session_participants-derived room-role header is the correct
        # source of truth for a missing header, instead of "this pod's own
        # auth boundary already gates every caller" (skuld.config.WsAuthConfig
        # .room_role_source, default "deployment" — every other backend).
        env["SKULD__WS_AUTH__ROOM_ROLE_SOURCE"] = "proxy"
        env.update(self._preview_env(session_id))
        env.update(mesh_environment(self._mesh if mesh is None else mesh))
        model = str(session.model or spec.values.get("model", "") or "").strip()
        env["SKULD__SESSION__MODEL"] = model
        env["SKULD__SESSION__WORKSPACE_DIR"] = str(workspace)
        env["SKULD__HOST"] = "127.0.0.1"
        env["SKULD__PORT"] = str(port)
        env.setdefault("SKULD__TRANSPORT", "sdk")
        env["SKULD__PERSISTENCE_MOUNT_PATH"] = str(self._workspaces_dir)
        # A local session has no per-session home mount: its agent runs with this
        # host HOME. Tell the broker so present-file accepts ~ files, and give it a
        # per-session staging dir outside the workspace (the cluster layout
        # {mount}/{id}/home does not exist here).
        env["SKULD__HOME_DIR"] = env.get("HOME") or str(Path.home())
        env["SKULD__PRESENTED_FILES_DIR"] = str(
            self._workspaces_dir / LOCAL_PRESENTED_FILES_DIR / session_id
        )

        # Volundr API URL so Skuld can post chronicles/timeline events back
        server_host = self._server_host
        server_port = self._server_port
        env["SKULD__VOLUNDR_API_URL"] = f"http://{server_host}:{server_port}"

        session_vals = spec.values.get("session", {})
        system_prompt = session_vals.get("systemPrompt", "")
        initial_prompt = session_vals.get("initialPrompt", "")
        effort = session_vals.get("reasoningEffort") or session_vals.get("reasoning_effort")
        if effort:
            env["SKULD__SESSION__REASONING_EFFORT"] = str(effort)
        if system_prompt:
            env["SKULD__SESSION__SYSTEM_PROMPT"] = system_prompt
        # The pinned persona document: Skuld projects it onto the harness on every start.
        if persona := session_vals.get("persona"):
            env["SKULD__SESSION__PERSONA"] = json.dumps(persona, separators=(",", ":"))
        if initial_prompt:
            env["SKULD__SESSION__INITIAL_PROMPT"] = initial_prompt
        env.update(workflow_session_environment(spec.values))

        # Claude transports still need the CLI binary location.
        if env.get("SKULD__CLI_TYPE", "claude") == "claude":
            env["SKULD__CLI_BINARY"] = self._resolve_claude_binary()

        # Last, so neither Forge's own environment nor launch-spec env can supply
        # (or keep) a session credential that this launch did not mint.
        _apply_forge_session_env(env, spec)
        # Pod-spec contributors run after _build_env; never pass Forge's capture
        # signing key to a child, even when a contributor supplies it explicitly.
        env.pop("FORGE_EXP_CAPTURE_SECRET", None)

        log_path = workspace / ".skuld.log"
        # Multiple sessions may share a workspace; retain diagnostics from every
        # broker while preserving the path consumed by aggregation and archives.
        log_file = log_path.open("a", encoding="utf-8")
        group_args = {} if process_group is None else {"process_group": process_group}
        try:
            process = await asyncio.create_subprocess_exec(
                *skuld_cmd,
                stdout=log_file,
                stderr=log_file,
                cwd=str(workspace),
                env=env,
                **group_args,
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

    def _resolve_skuld_command(self) -> list[str]:
        """Resolve the command to run a Skuld subprocess.

        From the compiled binary: reuse the same binary with 'platform skuld'.
        From source: use 'python -m skuld'.
        """
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

    @classmethod
    def _build_env(cls, spec: SessionSpec, workspace: Path) -> dict[str, str]:
        """Build environment variables for the Skuld process.

        The host process inherits the platform environment; the session-specific
        values from :meth:`_session_env` are layered on top.
        """
        env = dict(os.environ)
        for key in (
            "SKULD__SKIP_PERMISSIONS",
            "SKULD__APPROVAL_POLICY",
            "SKULD__SANDBOX",
        ):
            env.pop(key, None)
        env.update(cls._session_env(spec, workspace))
        # This signing key belongs only to Forge, never a session broker.
        env.pop("FORGE_EXP_CAPTURE_SECRET", None)
        return env

    @staticmethod
    def _session_env(spec: SessionSpec, workspace: Path) -> dict[str, str]:
        """Environment derived from the session spec alone (no host inheritance).

        Container-based managers use this directly so the platform's own
        environment never leaks into a sandbox.
        """
        env: dict[str, str] = {}
        env["SKULD__SESSION__WORKSPACE_DIR"] = str(workspace)

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

        # Contributors (integrations, model gateway routing) hand over env as the
        # Helm-shaped list; on a single host it is applied here, so a session
        # gets the same variables whichever runtime starts it.
        for entry in spec.values.get("envVars") or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                raise ValueError(f"envVars entries need a name: {entry!r}")
            env[str(entry["name"])] = str(entry.get("value", ""))

        session_values = spec.values.get("session", {})
        effort = session_values.get("reasoningEffort") or session_values.get("reasoning_effort")
        if effort:
            env["SKULD__SESSION__REASONING_EFFORT"] = str(effort)

        broker = spec.values.get("broker", {})
        if isinstance(broker, dict):
            # The same broker settings reach process, Docker, OpenShell and VM sessions.
            for key, field in {
                "historyReadTimeoutSeconds": "history_read_timeout_seconds",
                "historyBootstrapMaxFrames": "history_bootstrap_max_frames",
                "conversationRecentMaxTurns": "conversation_recent_max_turns",
                "conversationRecentMaxBytes": "conversation_recent_max_bytes",
                "conversationSnapshotMaxBytes": "conversation_snapshot_max_bytes",
                "liveFrameMaxBytes": "live_frame_max_bytes",
                "codexReceiveMaxBytes": "codex_receive_max_bytes",
                "historyHydrationEnabled": "history_hydration_enabled",
                "historyHydrationTimeoutSeconds": "history_hydration_timeout_seconds",
                "historyHydrationPageSize": "history_hydration_page_size",
                "historyHydrationMaxFrames": "history_hydration_max_frames",
                "historyHydrationMaxBytes": "history_hydration_max_bytes",
                "tmuxQuestionTranscriptMaxBytes": "tmux_question_transcript_max_bytes",
                "tmuxQuestionResultHistoryLimit": "tmux_question_result_history_limit",
                "tmuxNativeTextWaitSeconds": "tmux_native_text_wait_s",
                "tmuxNativeTextPollSeconds": "tmux_native_text_poll_s",
                "effortControlTimeoutSeconds": "effort_control_timeout_s",
                "museBin": "muse_bin",
                "pi": "pi",
            }.items():
                value = broker.get(key, broker.get(field))
                if value is not None:
                    env[f"SKULD__{field.upper()}"] = (
                        json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
                    )

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

            # Per-definition override of the node's claude_permission_mode (node config file).
            claude_permission_mode = broker.get(
                "claudePermissionMode", broker.get("claude_permission_mode")
            )
            if claude_permission_mode:
                env["SKULD__CLAUDE_PERMISSION_MODE"] = str(claude_permission_mode)

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

        mcp_servers = spec.values.get("mcpServers")
        if isinstance(mcp_servers, list) and mcp_servers:
            env["SKULD__MCP_SERVERS"] = json.dumps(mcp_servers)

        # Container/VM callers also use this spec-only environment builder.
        env.pop("FORGE_EXP_CAPTURE_SECRET", None)
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
                info.state = ProcessState.STOPPED
                if info.port is not None:
                    self._port_allocator.release(info.port)
                self._persist_state()
                if self._skuld_registry is not None:
                    unregister = getattr(self._skuld_registry, "unregister", None)
                    if callable(unregister):
                        unregister(session_id)
                logger.info("Claude process exited pid=%d session=%s", pid, session_id)
                ended = await self._end_unit_after_root(session_id, info)
                await self._notify_death(session_id)
                for member_id in ended:
                    await self._notify_death(member_id)
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

    def _capacity_remedy(self) -> str:
        """Where this runtime's session limit is raised, for the refusal message."""
        return "raise pod_manager.max_concurrent in this install's config.yaml and restart"

    def _capacity_snapshot(self, active: list[str]) -> SessionCapacity:
        return SessionCapacity(
            limit=self._max_concurrent,
            active=len(active),
            remedy=self._capacity_remedy(),
        )

    async def capacity(self) -> SessionCapacity | None:
        """The concurrent-session limit and how much of it is in use; None with no limit."""
        if self._max_concurrent is None:
            return None
        return self._capacity_snapshot(self._reconcile_active())

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
                reaped.append(sid)
        if reaped:
            self._persist_state()
            logger.warning(
                "Reaped %d phantom session(s) holding concurrent slots (process dead): %s",
                len(reaped),
                ", ".join(s[:8] for s in reaped),
            )
        return self._active_sessions()
