"""Docker-container PodManager — one Skuld container per Forge session.

Used by docker mode, where the platform itself runs inside a container with the
host's Docker socket mounted. Each session becomes a sibling container from the
``skuld`` image, attached to the platform's compose network so the session
proxy can reach it by container name.

This reuses :class:`LocalProcessPodManager` for everything that is not
process-shaped (workspace provisioning, git clone, local mounts, state file,
concurrency cap, env derivation) and swaps the subprocess for a container.

Requirements the composition root must honour:

* ``workspaces_dir`` must be the same path on the host and inside the platform
  container (bind-mounted 1:1), because Docker resolves bind sources on the
  host.
* The platform's URL must be reachable from a session container
  (``platform_url``; on a compose network that is ``http://niuu:8080``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import docker
import httpx
from docker.errors import DockerException, ImageNotFound, NotFound

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.local_process import (
    FlockPortPlan,
    LocalProcessPodManager,
    ProcessInfo,
    ProcessState,
)
from volundr.domain.models import SessionStatus

if TYPE_CHECKING:
    from volundr.domain.models import Session, SessionSpec

logger = logging.getLogger(__name__)

DEFAULT_SKULD_IMAGE = "ghcr.io/niuulabs/skuld:dev"
DEFAULT_BROKER_PORT = 8081
DEFAULT_CONTAINER_PREFIX = "niuu-session-"
DEFAULT_SANDBOX_SESSIONS_DIR = "/volundr/sessions"
DEFAULT_SANDBOX_HOME = "/home/skuld"
DEFAULT_PLATFORM_URL = "http://host.docker.internal:8080"
DEFAULT_LOG_TAIL = 100
DEFAULT_MONITOR_INTERVAL_SECONDS = 2.0
DEFAULT_READY_POLL_SECONDS = 0.5
DEFAULT_READY_PROBE_TIMEOUT_SECONDS = 2.0
MANAGED_BY = "docker_container"
LABEL_SESSION = "niuu.session-id"
LABEL_MANAGED_BY = "niuu.managed-by"


class _NetworkRegistry:
    """Registry facade for network mode.

    In network mode the proxy must dial the container by name, so the loopback
    port registration the base class performs is suppressed while unregister
    still propagates.
    """

    def __init__(self, registry: object) -> None:
        self._registry = registry

    def register(self, session_id: str, port: int) -> None:
        del session_id, port

    def unregister(self, session_id: str) -> None:
        unregister = getattr(self._registry, "unregister", None)
        if callable(unregister):
            unregister(session_id)


class DockerContainerPodManager(LocalProcessPodManager):
    """Run each Forge session as a Skuld container on the host Docker daemon."""

    def __init__(
        self,
        *,
        skuld_image: str = DEFAULT_SKULD_IMAGE,
        network: str = "",
        platform_url: str = DEFAULT_PLATFORM_URL,
        docker_base_url: str = "",
        container_prefix: str = DEFAULT_CONTAINER_PREFIX,
        broker_port: int = DEFAULT_BROKER_PORT,
        sandbox_sessions_dir: str = DEFAULT_SANDBOX_SESSIONS_DIR,
        sandbox_home: str = DEFAULT_SANDBOX_HOME,
        run_as_host_user: bool = True,
        pull_missing: bool = True,
        log_tail: int = DEFAULT_LOG_TAIL,
        monitor_interval_seconds: float = DEFAULT_MONITOR_INTERVAL_SECONDS,
        ready_poll_seconds: float = DEFAULT_READY_POLL_SECONDS,
        ready_probe_timeout_seconds: float = DEFAULT_READY_PROBE_TIMEOUT_SECONDS,
        **kwargs: Any,
    ) -> None:
        # The base constructor recovers persisted sessions through
        # _is_recoverable_local_process, which needs the client and naming below.
        self._skuld_image = str(skuld_image)
        self._network = str(network).strip()
        self._platform_url = str(platform_url).rstrip("/")
        self._container_prefix = str(container_prefix)
        self._broker_port = int(broker_port)
        self._sandbox_sessions_dir = str(sandbox_sessions_dir).rstrip("/")
        self._sandbox_home = str(sandbox_home)
        self._run_as_host_user = _as_bool(run_as_host_user)
        self._pull_missing = _as_bool(pull_missing)
        self._log_tail = int(log_tail)
        self._monitor_interval = float(monitor_interval_seconds)
        self._ready_poll = float(ready_poll_seconds)
        self._ready_probe_timeout = float(ready_probe_timeout_seconds)
        # Liveness as last observed by the monitor tasks. Consulted by the
        # synchronous base-class hooks so the event loop never waits on the
        # Docker API while serving requests.
        self._alive: dict[str, bool] = {}
        # Sessions whose broker has answered /health at least once.
        self._ready: set[str] = set()
        self._client = (
            docker.DockerClient(base_url=str(docker_base_url))
            if str(docker_base_url).strip()
            else docker.from_env()
        )
        super().__init__(**kwargs)
        for info in self._processes.values():
            info.managed_by = MANAGED_BY
            if info.state == ProcessState.RUNNING:
                # Recovered from the state file: the broker was up before the
                # platform restarted and there is no monitor to re-observe it.
                self._ready.add(info.session_id)

    # ------------------------------------------------------------------
    # Naming and lookup
    # ------------------------------------------------------------------

    def container_name(self, session_id: str) -> str:
        return f"{self._container_prefix}{session_id}"

    @staticmethod
    def _synthetic_pid(session_id: str) -> int:
        """Stable positive int standing in for a PID in the shared state file."""
        digest = hashlib.sha1(session_id.encode(), usedforsecurity=False).hexdigest()
        return int(digest[:8], 16) or 1

    def _session_for_pid(self, pid: int) -> str | None:
        for session_id, info in self._processes.items():
            if info.pid == pid:
                return session_id
        return None

    def _get_container(self, session_id: str) -> Any | None:
        try:
            return self._client.containers.get(self.container_name(session_id))
        except NotFound:
            return None

    def _container_running(self, session_id: str) -> bool:
        container = self._get_container(session_id)
        if container is None:
            return False
        container.reload()
        return str(container.status) == "running"

    # ------------------------------------------------------------------
    # Proxy routing
    # ------------------------------------------------------------------

    def set_skuld_registry(self, registry: object) -> None:
        if not self._network:
            super().set_skuld_registry(registry)
            return
        # Network mode: no loopback port to register. Routing goes through
        # session_proxy_target, which the platform's composite resolver calls.
        self._skuld_registry = _NetworkRegistry(registry)

    def session_proxy_target(self, session: Session) -> SessionProxyTarget | None:
        """Where the session proxy should dial for this session (network mode only)."""
        if not self._network:
            return None
        session_id = str(session.id)
        info = self._processes.get(session_id)
        if info is None or info.state != ProcessState.RUNNING:
            return None
        name = self.container_name(session_id)
        return SessionProxyTarget(
            service_url=f"http://{name}:{self._broker_port}",
            connect_host=name,
            connect_port=self._broker_port,
        )

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------

    def _broker_health_url(self, session_id: str) -> str | None:
        if self._network:
            return f"http://{self.container_name(session_id)}:{self._broker_port}/health"
        info = self._processes.get(session_id)
        if info is None or info.port is None:
            return None
        return f"http://127.0.0.1:{info.port}/health"

    async def _broker_healthy(self, session_id: str) -> bool:
        url = self._broker_health_url(session_id)
        if url is None:
            return False
        try:
            async with httpx.AsyncClient(timeout=self._ready_probe_timeout) as client:
                response = await client.get(url)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def status(self, session: Session) -> SessionStatus:
        """Like the base class, but RUNNING only once the broker has answered."""
        base = await super().status(session)
        if base == SessionStatus.RUNNING and str(session.id) not in self._ready:
            return SessionStatus.PROVISIONING
        return base

    async def wait_for_ready(self, session: Session, timeout: float) -> SessionStatus:
        """Ready means the container runs *and* its broker answers ``/health``."""
        session_id = str(session.id)
        elapsed = 0.0
        while elapsed < timeout:
            info = self._processes.get(session_id)
            if info is None or info.state == ProcessState.FAILED:
                return SessionStatus.FAILED
            if info.state == ProcessState.STOPPED:
                return SessionStatus.STOPPED
            if info.state == ProcessState.RUNNING and await self._broker_healthy(session_id):
                self._ready.add(session_id)
                return SessionStatus.RUNNING
            await asyncio.sleep(self._ready_poll)
            elapsed += self._ready_poll
        logger.warning("Session %s broker did not answer /health within %.0fs", session_id, timeout)
        return SessionStatus.FAILED

    # ------------------------------------------------------------------
    # Container lifecycle (replaces the subprocess hooks of the base class)
    # ------------------------------------------------------------------

    def _sandbox_workspace(self, session_id: str) -> str:
        return f"{self._sandbox_sessions_dir}/{session_id}/workspace"

    def _session_home_dir(self, session_id: str) -> Path:
        return self._workspaces_dir / f"{session_id}.home"

    def _container_environment(
        self,
        session: Session,
        spec: SessionSpec,
        workspace: Path,
    ) -> dict[str, str]:
        session_id = str(session.id)
        sandbox_workspace = self._sandbox_workspace(session_id)
        env = self._session_env(spec, Path(sandbox_workspace))
        if spec.pod_spec and spec.pod_spec.env:
            for entry in spec.pod_spec.env:
                if name := entry.get("name"):
                    env[name] = entry.get("value", "")

        env["SESSION_ID"] = session_id
        env["WORKSPACE_DIR"] = sandbox_workspace
        env["HOME"] = self._sandbox_home
        env["SKULD__SESSION__ID"] = session_id
        env["SKULD__SESSION__NAME"] = session.name
        if session.owner_id:
            env["SKULD__SESSION__OWNER_ID"] = session.owner_id
        if session.tenant_id:
            env["SKULD__SESSION__TENANT_ID"] = session.tenant_id
        model = str(session.model or spec.values.get("model", "") or "").strip()
        env["SKULD__SESSION__MODEL"] = model
        env["SKULD__SESSION__WORKSPACE_DIR"] = sandbox_workspace
        env["SKULD__HOST"] = "0.0.0.0"
        env["SKULD__PORT"] = str(self._broker_port)
        env.setdefault("SKULD__TRANSPORT", "sdk")
        env["SKULD__PERSISTENCE_MOUNT_PATH"] = self._sandbox_sessions_dir
        env["SKULD__VOLUNDR_API_URL"] = self._platform_url

        session_vals = spec.values.get("session", {})
        if isinstance(session_vals, dict):
            system_prompt = session_vals.get("systemPrompt", "")
            initial_prompt = session_vals.get("initialPrompt", "")
            if system_prompt:
                env["SKULD__SESSION__SYSTEM_PROMPT"] = system_prompt
            if initial_prompt:
                env["SKULD__SESSION__INITIAL_PROMPT"] = initial_prompt
        del workspace
        return env

    @staticmethod
    def _host_path_binds(spec: SessionSpec) -> dict[str, dict[str, str]]:
        """Translate ``hostPath`` volumes contributed to the pod spec into binds.

        Secret injection adapters describe what to mount in Kubernetes terms;
        on a single host the same paths are bound straight into the container.
        Anything that is not a ``hostPath`` volume cannot be honoured here and
        is a configuration error, not something to skip.
        """
        if spec.pod_spec is None:
            return {}
        host_paths: dict[str, tuple[str, str]] = {}
        for volume in spec.pod_spec.volumes:
            name = str(volume.get("name", ""))
            host_path = volume.get("hostPath")
            if not isinstance(host_path, dict) or not host_path.get("path"):
                raise ValueError(
                    f"Volume {name!r} is not a hostPath volume; the docker session "
                    "runtime can only bind host paths (check secret_injection.adapter)"
                )
            host_paths[name] = (str(host_path["path"]), str(host_path.get("type", "")))

        binds: dict[str, dict[str, str]] = {}
        for mount in spec.pod_spec.volume_mounts:
            name = str(mount.get("name", ""))
            if name not in host_paths:
                raise ValueError(f"Volume mount {name!r} references an undeclared volume")
            path, path_type = host_paths[name]
            source = Path(path)
            if path_type == "DirectoryOrCreate":
                source.mkdir(parents=True, exist_ok=True)
            elif not source.exists():
                raise FileNotFoundError(f"Volume {name!r} host path {path} does not exist")
            binds[str(source)] = {
                "bind": str(mount["mountPath"]),
                "mode": "ro" if mount.get("readOnly") else "rw",
            }
        return binds

    def _run_kwargs(
        self,
        session: Session,
        spec: SessionSpec,
        workspace: Path,
        port: int,
    ) -> dict[str, Any]:
        session_id = str(session.id)
        home_dir = self._session_home_dir(session_id)
        home_dir.mkdir(parents=True, exist_ok=True)
        volumes: dict[str, dict[str, str]] = {
            str(workspace): {"bind": self._sandbox_workspace(session_id), "mode": "rw"},
            str(home_dir): {"bind": self._sandbox_home, "mode": "rw"},
        }
        volumes.update(self._host_path_binds(spec))
        kwargs: dict[str, Any] = {
            "image": self._skuld_image,
            "name": self.container_name(session_id),
            "detach": True,
            "init": True,
            "labels": {LABEL_SESSION: session_id, LABEL_MANAGED_BY: MANAGED_BY},
            "environment": self._container_environment(session, spec, workspace),
            "volumes": volumes,
            "extra_hosts": {"host.docker.internal": "host-gateway"},
        }
        if self._run_as_host_user:
            kwargs["user"] = f"{os.getuid()}:{os.getgid()}"
        if self._network:
            kwargs["network"] = self._network
        else:
            kwargs["ports"] = {f"{self._broker_port}/tcp": ("127.0.0.1", port)}
        return kwargs

    def _run_container(self, run_kwargs: dict[str, Any]) -> Any:
        try:
            return self._client.containers.run(**run_kwargs)
        except ImageNotFound:
            if not self._pull_missing:
                raise
            logger.info("Pulling session image %s", self._skuld_image)
            self._client.images.pull(self._skuld_image)
            return self._client.containers.run(**run_kwargs)

    async def _spawn_skuld(
        self,
        session: Session,
        spec: SessionSpec,
        workspace: Path,
        port: int,
        flock_plan: FlockPortPlan | None = None,
    ) -> int:
        """Start the session container; returns a synthetic PID for the state file."""
        if flock_plan is not None:
            raise RuntimeError(
                "Flock sidecars are not supported by DockerContainerPodManager; "
                "run the session without a flock or use the local-process pod manager."
            )
        session_id = str(session.id)
        stale = await asyncio.to_thread(self._get_container, session_id)
        if stale is not None:
            await asyncio.to_thread(stale.remove, force=True)

        run_kwargs = self._run_kwargs(session, spec, workspace, port)
        container = await asyncio.to_thread(self._run_container, run_kwargs)
        await asyncio.to_thread(container.reload)
        if str(container.status) in {"exited", "dead"}:
            logs = await asyncio.to_thread(container.logs, tail=self._log_tail)
            text = logs.decode("utf-8", errors="replace") if isinstance(logs, bytes) else str(logs)
            raise RuntimeError(
                f"Session container {container.name} exited immediately:\n{text.strip()}"
            )
        logger.info(
            "Started session container %s image=%s session=%s",
            container.name,
            self._skuld_image,
            session_id,
        )
        info = self._processes.get(session_id)
        if info is not None:
            info.managed_by = MANAGED_BY
            if self._network:
                # The proxy dials by container name; a persisted loopback port
                # would be tried first and shadow the resolver.
                self._port_allocator.release(port)
                info.port = None
        self._alive[session_id] = True
        return self._synthetic_pid(session_id)

    async def _monitor_process(self, session_id: str, pid: int) -> None:
        """Watch the container and mark the session stopped when it exits."""
        del pid
        try:
            while await asyncio.to_thread(self._container_running, session_id):
                self._alive[session_id] = True
                if session_id not in self._ready and await self._broker_healthy(session_id):
                    self._ready.add(session_id)
                await asyncio.sleep(self._monitor_interval)
            self._alive[session_id] = False
            self._ready.discard(session_id)

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
                logger.info("Session container exited session=%s", session_id)
                await self._notify_death(session_id)
        except asyncio.CancelledError:
            return

    async def _terminate_process(self, pid: int) -> None:
        """Stop and remove the session container."""
        session_id = self._session_for_pid(pid)
        if session_id is None:
            return
        self._alive[session_id] = False
        self._ready.discard(session_id)
        container = await asyncio.to_thread(self._get_container, session_id)
        if container is None:
            return
        try:
            await asyncio.to_thread(container.stop, timeout=self._stop_timeout)
        except DockerException as exc:
            logger.warning("Stopping session container %s failed: %s", container.name, exc)
        try:
            await asyncio.to_thread(container.remove, force=True)
        except NotFound:
            return

    def _persist_state(self) -> None:
        if self._network:
            # The proxy recovers loopback ports from this file and would try
            # 127.0.0.1:<port> before the container-name target.
            for info in self._processes.values():
                info.port = None
        super()._persist_state()

    def _is_process_alive(self, pid: int) -> bool:  # type: ignore[override]
        session_id = self._session_for_pid(pid)
        if session_id is None:
            return False
        cached = self._alive.get(session_id)
        if cached is not None:
            return cached
        # No monitor has reported yet (e.g. a session recovered from the state
        # file); ask Docker once and remember the answer.
        alive = self._container_running(session_id)
        self._alive[session_id] = alive
        return alive

    def _is_recoverable_local_process(self, info: ProcessInfo) -> bool:
        if info.managed_by not in {MANAGED_BY, "local_process"}:
            return False
        return self._container_running(info.session_id)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
