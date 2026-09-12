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
from docker.errors import DockerException, ImageNotFound, NotFound

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.local_process import (
    READY_POLL_INTERVAL,
    FlockPortPlan,
    LocalProcessPodManager,
    ProcessInfo,
    ProcessState,
)

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
        self._client = (
            docker.DockerClient(base_url=str(docker_base_url))
            if str(docker_base_url).strip()
            else docker.from_env()
        )
        super().__init__(**kwargs)
        for info in self._processes.values():
            info.managed_by = MANAGED_BY

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
        set_resolver = getattr(registry, "set_target_resolver", None)
        if callable(set_resolver):
            set_resolver(self._resolve_proxy_target)
        self._skuld_registry = _NetworkRegistry(registry)

    async def _resolve_proxy_target(self, session_id: str) -> SessionProxyTarget | None:
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
        kwargs: dict[str, Any] = {
            "image": self._skuld_image,
            "name": self.container_name(session_id),
            "detach": True,
            "init": True,
            "labels": {LABEL_SESSION: session_id, LABEL_MANAGED_BY: MANAGED_BY},
            "environment": self._container_environment(session, spec, workspace),
            "volumes": {
                str(workspace): {"bind": self._sandbox_workspace(session_id), "mode": "rw"},
                str(home_dir): {"bind": self._sandbox_home, "mode": "rw"},
            },
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
        stale = self._get_container(session_id)
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
        return self._synthetic_pid(session_id)

    async def _monitor_process(self, session_id: str, pid: int) -> None:
        """Watch the container and mark the session stopped when it exits."""
        del pid
        try:
            while await asyncio.to_thread(self._container_running, session_id):
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
                logger.info("Session container exited session=%s", session_id)
                await self._notify_death(session_id)
        except asyncio.CancelledError:
            return

    async def _terminate_process(self, pid: int) -> None:
        """Stop and remove the session container."""
        session_id = self._session_for_pid(pid)
        if session_id is None:
            return
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

    def _is_process_alive(self, pid: int) -> bool:  # type: ignore[override]
        session_id = self._session_for_pid(pid)
        if session_id is None:
            return False
        return self._container_running(session_id)

    def _is_recoverable_local_process(self, info: ProcessInfo) -> bool:
        if info.managed_by not in {MANAGED_BY, "local_process"}:
            return False
        return self._container_running(info.session_id)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
