"""OpenShell adapter for local Forge session runtime management.

Constructor accepts plain kwargs (dynamic adapter pattern):
    adapter: "volundr.adapters.outbound.openshell.OpenShellPodManager"
    gateway_url: ""
    sandbox_image: "ghcr.io/niuulabs/skuld:0.2.0"
    workspaces_dir: "~/.niuu/workspaces"
    state_file: "~/.niuu/openshell-forge-state.json"
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import os
import re
import shlex
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from volundr.adapters.outbound.local_process import (
    LocalProcessPodManager,
    SdkPortAllocator,
    _inject_token_into_url,
    _local_workspace_from_repo,
)
from volundr.domain.models import GitSource, LocalMountSource, Session, SessionSpec, SessionStatus
from volundr.domain.ports import PodManager, PodStartResult

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY_URL = ""
DEFAULT_SANDBOX_IMAGE = "ghcr.io/niuulabs/skuld:0.2.0"
DEFAULT_WORKSPACES_DIR = "~/.niuu/workspaces"
DEFAULT_STATE_FILE = "~/.niuu/openshell-forge-state.json"
DEFAULT_SDK_PORT_START = 9200
DEFAULT_SANDBOX_COMMAND = ["/opt/venv/bin/python", "-m", "skuld"]
DEFAULT_MAX_CONCURRENT = 8
DEFAULT_STOP_TIMEOUT = 30.0
DEFAULT_COMMAND_TIMEOUT = 60.0
READY_POLL_INTERVAL = 1.0
DEFAULT_BROKER_HEALTH_PATH = "/health"


class OpenShellState(StrEnum):
    """Small internal state stored for OpenShell-backed sessions."""

    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class OpenShellSessionInfo:
    """Persisted OpenShell sandbox metadata."""

    session_id: str
    sandbox_name: str
    port: int | None = None
    workspace: str = ""
    runtime: str = ""
    state: OpenShellState = OpenShellState.STARTING
    error: str | None = None
    managed_by: str = "openshell"
    start_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> OpenShellSessionInfo:
        return OpenShellSessionInfo(
            session_id=str(data.get("session_id") or data.get("id") or ""),
            sandbox_name=str(data.get("sandbox_name") or data.get("name") or ""),
            port=data.get("port"),
            workspace=str(data.get("workspace") or data.get("workspace_dir") or ""),
            runtime=str(data.get("runtime") or ""),
            state=OpenShellState(str(data.get("state") or data.get("status") or "stopped")),
            error=data.get("error"),
            managed_by=str(data.get("managed_by") or "openshell"),
            start_complete=_as_bool(data.get("start_complete")),
        )


class OpenShellClient:
    """Tiny async boundary around the OpenShell CLI.

    OpenShell's local gateway API is not documented as a stable public contract yet;
    the CLI with JSON output is the replaceable adapter boundary for this spike.
    """

    def __init__(
        self,
        *,
        binary: str = "openshell",
        gateway_url: str = DEFAULT_GATEWAY_URL,
        gateway_name: str = "local",
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
    ):
        self._binary = binary
        self._gateway_url = gateway_url
        self._gateway_name = gateway_name
        self._command_timeout = float(command_timeout)
        self._service_forwards: dict[tuple[str, int], asyncio.subprocess.Process] = {}

    async def ensure_gateway(self) -> None:
        if not self._gateway_url:
            return
        result = await self._run(
            "gateway",
            "add",
            self._gateway_url,
            "--local",
            "--name",
            self._gateway_name,
            check=False,
        )
        if result.returncode == 0:
            return
        stderr = result.stderr.lower()
        if "already" in stderr or "exists" in stderr:
            return
        raise RuntimeError(result.stderr.strip() or "openshell gateway add failed")

    async def create_sandbox(
        self,
        *,
        name: str,
        image: str,
        command: Sequence[str],
        env: dict[str, str],
        labels: dict[str, str],
        cpu: str = "",
        memory: str = "",
        driver_config_json: str = "",
        uploads: Sequence[str] = (),
        upload_no_git_ignore: bool = False,
        policy_file: str = "",
    ) -> dict[str, Any]:
        args: list[str] = ["sandbox", "create", "--name", name, "--from", image]
        if cpu:
            args.extend(["--cpu", cpu])
        if memory:
            args.extend(["--memory", memory])
        for key, value in sorted(env.items()):
            args.extend(["--env", f"{key}={value}"])
        for key, value in sorted(labels.items()):
            args.extend(["--label", f"{key}={value}"])
        if policy_file:
            args.extend(["--policy", policy_file])
        if driver_config_json:
            args.extend(["--driver-config-json", driver_config_json])
        if upload_no_git_ignore:
            args.append("--no-git-ignore")
        for upload in uploads:
            args.extend(["--upload", upload])
        args.extend(["--", *command])
        await self._run(*args)
        return {}

    async def get_sandbox(self, name: str) -> dict[str, Any] | None:
        sandboxes = await self.list_sandboxes()
        for sandbox in sandboxes:
            if not isinstance(sandbox, dict):
                continue
            if _sandbox_name(sandbox) == name:
                return sandbox
        return None

    async def list_sandboxes(self, *, selector: str = "") -> list[dict[str, Any]]:
        args = ["sandbox", "list", "-o", "json"]
        if selector:
            args.extend(["--selector", selector])
        result = await self._run(*args)
        return _loads_json_list(result.stdout)

    async def delete_sandbox(self, name: str) -> bool:
        result = await self._run("sandbox", "delete", name, check=False)
        if result.returncode == 0:
            return True
        stderr = result.stderr.lower()
        if "notfound" in stderr or "not found" in stderr or "404" in stderr:
            return False
        raise RuntimeError(result.stderr.strip() or f"openshell sandbox delete {name} failed")

    async def forward_start(self, *, sandbox_name: str, port: int) -> None:
        await self._run("forward", "start", str(port), sandbox_name, "-d")

    async def forward_service_start(self, *, sandbox_name: str, port: int) -> None:
        key = (sandbox_name, port)
        existing = self._service_forwards.get(key)
        if existing is not None and existing.returncode is None:
            return
        process = await asyncio.create_subprocess_exec(
            self._binary,
            "forward",
            "service",
            sandbox_name,
            "--target-port",
            str(port),
            "--local",
            f"127.0.0.1:{port}",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(0.2)
        if process.returncode is not None:
            raise RuntimeError(f"{self._binary} forward service {sandbox_name} failed")
        self._service_forwards[key] = process

    async def forward_stop(self, *, sandbox_name: str, port: int, mode: str = "start") -> None:
        key = (sandbox_name, port)
        process = self._service_forwards.pop(key, None)
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
            return
        if mode == "start":
            await self._run("forward", "stop", str(port), sandbox_name, check=False)

    async def _run_json(self, *args: str) -> dict[str, Any]:
        result = await self._run(*args, "-o", "json")
        if not result.stdout.strip():
            return {}
        return _loads_json(result.stdout)

    async def _run(self, *args: str, check: bool = True) -> _CompletedProcess:
        process = await asyncio.create_subprocess_exec(
            self._binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                self._command_timeout,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        result = _CompletedProcess(
            returncode=process.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"{self._binary} {' '.join(args)} failed")
        return result


@dataclass(frozen=True)
class _CompletedProcess:
    returncode: int
    stdout: str
    stderr: str


def _loads_json(value: str) -> dict[str, Any]:
    raw = json.loads(value)
    if isinstance(raw, dict):
        return raw
    raise RuntimeError("OpenShell returned JSON that is not an object")


def _loads_json_list(value: str) -> list[dict[str, Any]]:
    raw = json.loads(value)
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in ("items", "sandboxes", "data"):
            items = raw.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    raise RuntimeError("OpenShell returned JSON that is not a sandbox list")


def _sandbox_name(sandbox: dict[str, Any]) -> str:
    metadata = sandbox.get("metadata")
    if isinstance(metadata, dict):
        name = metadata.get("name")
        if name:
            return str(name)
    return str(sandbox.get("name") or sandbox.get("id") or "")


class OpenShellPodManager(PodManager):
    """OpenShell-backed local implementation of the Volundr PodManager port."""

    def __init__(
        self,
        *,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        gateway_name: str = "local",
        openshell_binary: str = "openshell",
        sandbox_image: str = DEFAULT_SANDBOX_IMAGE,
        sandbox_command: list[str] | str | None = None,
        workspaces_dir: str = DEFAULT_WORKSPACES_DIR,
        state_file: str = DEFAULT_STATE_FILE,
        sdk_port_start: int = DEFAULT_SDK_PORT_START,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
        cpu: str = "",
        memory: str = "",
        mount_workspace: bool = True,
        upload_workspace: bool = False,
        upload_workspace_target: str | None = None,
        upload_no_git_ignore: bool = False,
        sandbox_uploads: Sequence[str] | str | None = None,
        sandbox_mounts: Sequence[dict[str, str] | str] | str | None = None,
        policy_file: str = "",
        sandbox_workspace: str = "/sandbox/workspace",
        ensure_gateway: bool = True,
        endpoint_exposure: str = "forward",
        forward_mode: str = "start",
        client: OpenShellClient | None = None,
        require_broker_ready: bool = True,
        broker_health_path: str = DEFAULT_BROKER_HEALTH_PATH,
        broker_health_timeout: float = 1.0,
        healthcheck: Callable[[int], Awaitable[bool]] | None = None,
        **_extra: object,
    ):
        self._gateway_url = gateway_url.rstrip("/")
        self._gateway_name = gateway_name
        self._sandbox_image = sandbox_image
        self._sandbox_command = _normalize_command(sandbox_command) or DEFAULT_SANDBOX_COMMAND
        self._workspaces_dir = Path(str(workspaces_dir)).expanduser()
        self._state_file = Path(str(state_file)).expanduser()
        self._port_allocator = SdkPortAllocator(start_port=int(sdk_port_start))
        self._max_concurrent = int(max_concurrent)
        self._stop_timeout = float(stop_timeout)
        self._cpu = cpu
        self._memory = memory
        self._mount_workspace = _as_bool(mount_workspace)
        self._upload_workspace = _as_bool(upload_workspace)
        self._upload_workspace_target = upload_workspace_target
        self._upload_no_git_ignore = _as_bool(upload_no_git_ignore)
        self._sandbox_uploads = _normalize_string_list(sandbox_uploads)
        self._sandbox_mounts = _normalize_mounts(sandbox_mounts)
        self._policy_file = policy_file
        self._sandbox_workspace = sandbox_workspace
        self._ensure_gateway = _as_bool(ensure_gateway)
        self._endpoint_exposure = endpoint_exposure
        self._forward_mode = forward_mode
        self._require_broker_ready = _as_bool(require_broker_ready)
        self._broker_health_path = broker_health_path or DEFAULT_BROKER_HEALTH_PATH
        self._broker_health_timeout = float(broker_health_timeout)
        self._healthcheck = healthcheck
        self._client = client or OpenShellClient(
            binary=openshell_binary,
            gateway_url=gateway_url,
            gateway_name=gateway_name,
            command_timeout=command_timeout,
        )
        self._sessions: dict[str, OpenShellSessionInfo] = {}
        self._skuld_registry: object | None = None
        self._load_state()
        for info in self._sessions.values():
            if info.port is not None and info.state in (
                OpenShellState.STARTING,
                OpenShellState.RUNNING,
            ):
                self._port_allocator._allocated.add(info.port)

    def set_skuld_registry(self, registry: object) -> None:
        """Inject the local Skuld proxy registry used by ``/s/{id}`` routes."""
        self._skuld_registry = registry
        register = getattr(registry, "register", None)
        if not callable(register):
            return
        for session_id, info in self._sessions.items():
            if info.port is None or info.state != OpenShellState.RUNNING:
                continue
            register(session_id, info.port)

    def initial_chat_endpoint(self, session: Session) -> str | None:
        info = self._sessions.get(str(session.id))
        if info and info.port:
            return self._chat_endpoint(session.id, info.port)
        return None

    async def start(self, session: Session, spec: SessionSpec) -> PodStartResult:
        session_id = str(session.id)
        active = [
            info
            for info in self._sessions.values()
            if info.state in (OpenShellState.STARTING, OpenShellState.RUNNING)
        ]
        if len(active) >= self._max_concurrent:
            raise RuntimeError(
                f"Max concurrent OpenShell sessions ({self._max_concurrent}) reached"
            )

        if self._ensure_gateway:
            await self._client.ensure_gateway()

        workspace = await self._provision_workspace(session, spec)
        port = self._port_allocator.allocate()
        sandbox_name = self._sandbox_name(session)
        runtime = self._runtime_from_spec(spec)
        info = OpenShellSessionInfo(
            session_id=session_id,
            sandbox_name=sandbox_name,
            port=port,
            workspace=str(workspace),
            runtime=runtime,
            state=OpenShellState.STARTING,
            start_complete=False,
        )
        self._sessions[session_id] = info
        self._persist_state()

        env = self._build_sandbox_env(session, spec, workspace, port)
        driver_config_json = self._driver_config_json(workspace)
        uploads = self._uploads(workspace)
        try:
            await self._client.create_sandbox(
                name=sandbox_name,
                image=self._sandbox_image,
                command=self._sandbox_command,
                env=env,
                labels={
                    "app.kubernetes.io/managed-by": "volundr",
                    "volundr.niuu.io/session": session_id,
                    "volundr.niuu.io/runtime": runtime,
                },
                cpu=self._cpu,
                memory=self._memory,
                driver_config_json=driver_config_json,
                uploads=uploads,
                upload_no_git_ignore=self._upload_no_git_ignore,
                policy_file=self._policy_file,
            )
            if self._endpoint_exposure == "forward":
                await self._start_forward(sandbox_name=sandbox_name, port=port)
            if await self._broker_is_ready(info):
                info.state = OpenShellState.RUNNING
            info.start_complete = True
            self._persist_state()
            self._register_skuld_port(session_id, port)
        except Exception as exc:
            info.state = OpenShellState.FAILED
            info.error = str(exc)
            self._port_allocator.release(port)
            self._persist_state()
            raise

        return PodStartResult(
            chat_endpoint=self._chat_endpoint(session.id, port),
            code_endpoint=self._code_endpoint(sandbox_name, workspace),
            pod_name=sandbox_name,
        )

    async def stop(self, session: Session) -> bool:
        info = self._sessions.get(str(session.id))
        if info is None:
            return False
        if info.port is not None and self._endpoint_exposure == "forward":
            await self._client.forward_stop(
                sandbox_name=info.sandbox_name,
                port=info.port,
                mode=self._forward_mode,
            )
        stopped = await asyncio.wait_for(
            self._client.delete_sandbox(info.sandbox_name),
            timeout=self._stop_timeout,
        )
        if info.port is not None:
            self._unregister_skuld_port(str(session.id))
            self._port_allocator.release(info.port)
        info.state = OpenShellState.STOPPED
        self._persist_state()
        return stopped

    async def status(self, session: Session) -> SessionStatus:
        info = self._sessions.get(str(session.id))
        if info is None:
            return SessionStatus.STOPPED
        sandbox = await self._client.get_sandbox(info.sandbox_name)
        if sandbox is None:
            info.state = OpenShellState.STOPPED
            self._unregister_skuld_port(str(session.id))
            self._persist_state()
            return SessionStatus.STOPPED
        status = self._map_status(sandbox)
        if status == SessionStatus.RUNNING and not await self._broker_is_ready(info):
            status = SessionStatus.PROVISIONING
        if status == SessionStatus.RUNNING and not info.start_complete:
            status = SessionStatus.PROVISIONING
        info.state = _state_from_session_status(status)
        if info.port is not None:
            if info.state == OpenShellState.RUNNING:
                self._register_skuld_port(str(session.id), info.port)
            elif info.state in (OpenShellState.STOPPED, OpenShellState.FAILED):
                self._unregister_skuld_port(str(session.id))
        self._persist_state()
        return status

    async def wait_for_ready(self, session: Session, timeout: float) -> SessionStatus:
        elapsed = 0.0
        while elapsed < timeout:
            status = await self.status(session)
            if status in (SessionStatus.RUNNING, SessionStatus.FAILED, SessionStatus.STOPPED):
                return status
            await asyncio.sleep(READY_POLL_INTERVAL)
            elapsed += READY_POLL_INTERVAL
        return SessionStatus.FAILED

    async def _start_forward(self, *, sandbox_name: str, port: int) -> None:
        if self._forward_mode == "service":
            await self._client.forward_service_start(sandbox_name=sandbox_name, port=port)
            return
        await self._client.forward_start(sandbox_name=sandbox_name, port=port)

    def _register_skuld_port(self, session_id: str, port: int) -> None:
        if self._skuld_registry is None:
            return
        register = getattr(self._skuld_registry, "register", None)
        if callable(register):
            register(session_id, port)

    def _unregister_skuld_port(self, session_id: str) -> None:
        if self._skuld_registry is None:
            return
        unregister = getattr(self._skuld_registry, "unregister", None)
        if callable(unregister):
            unregister(session_id)

    async def _provision_workspace(self, session: Session, spec: SessionSpec) -> Path:
        if isinstance(session.source, LocalMountSource):
            if session.source.local_path:
                path = Path(session.source.local_path).expanduser().resolve()
                if not path.is_dir():
                    raise RuntimeError(f"local path {path!r} is not a directory")
                return path
            if session.source.paths:
                workspace = self._workspaces_dir / str(session.id)
                workspace.mkdir(parents=True, exist_ok=True)
                for mapping in session.source.paths:
                    host = Path(mapping.host_path).expanduser()
                    if not host.exists():
                        continue
                    link_name = mapping.mount_path.strip("/").replace("/", "-") or host.name
                    link = workspace / link_name
                    if not link.exists():
                        link.symlink_to(host)
                return workspace

        if isinstance(session.source, GitSource) and session.source.repo:
            local_workspace = _local_workspace_from_repo(session.source.repo)
            if local_workspace is not None:
                workspace = local_workspace.expanduser().resolve()
                if not workspace.is_dir():
                    raise RuntimeError(f"local repo path {workspace!r} is not a directory")
                return workspace
            workspace = self._workspaces_dir / str(session.id)
            workspace.mkdir(parents=True, exist_ok=True)
            if not any(workspace.iterdir()):
                await self._clone_repo(session.source, workspace, spec)
            return workspace

        workspace = self._workspaces_dir / str(session.id)
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    async def _clone_repo(self, source: GitSource, workspace: Path, spec: SessionSpec) -> None:
        token = spec.values.get("git_token", "")
        clone_url = _inject_token_into_url(source.repo, str(token))
        args = ["git", "clone", "--depth", "1", clone_url, str(workspace / "repo")]
        if source.branch:
            args[2:2] = ["--branch", source.branch]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            error = stderr.decode(errors="replace")
            error = re.sub(r"://[^@]+@", "://***@", error)
            raise RuntimeError(f"Git clone failed: {error}")

    def _build_sandbox_env(
        self,
        session: Session,
        spec: SessionSpec,
        workspace: Path,
        port: int,
    ) -> dict[str, str]:
        env = LocalProcessPodManager._build_env(spec, workspace)
        env["SKULD__SESSION__ID"] = str(session.id)
        env["SKULD__SESSION__NAME"] = session.name
        env["SKULD__SESSION__WORKSPACE_DIR"] = self._sandbox_workspace
        env["SKULD__HOST"] = "0.0.0.0"
        env["SKULD__PORT"] = str(port)
        env["SKULD__PERSISTENCE_MOUNT_PATH"] = str(Path(self._sandbox_workspace).parent)
        server_host = os.environ.get("NIUU_SERVER_HOST", "127.0.0.1")
        server_port = os.environ.get("NIUU_SERVER_PORT", "8080")
        env["SKULD__VOLUNDR_API_URL"] = f"http://{server_host}:{server_port}"
        if session.owner_id:
            env["SKULD__SESSION__OWNER_ID"] = session.owner_id
        if session.tenant_id:
            env["SKULD__SESSION__TENANT_ID"] = session.tenant_id
        if session.model:
            env["SKULD__SESSION__MODEL"] = session.model
        sandbox_env = {key: value for key, value in env.items() if _safe_env_var(key, value)}
        extra_env = spec.values.get("env", {})
        if isinstance(extra_env, dict):
            for key, value in extra_env.items():
                key_str = str(key)
                value_str = str(value)
                if "\x00" not in key_str and "\x00" not in value_str:
                    sandbox_env[key_str] = value_str
        return sandbox_env

    def _driver_config_json(self, workspace: Path) -> str:
        mounts: list[dict[str, str]] = []
        if self._mount_workspace:
            mounts.append(
                {
                    "type": "bind",
                    "source": str(workspace),
                    "target": self._sandbox_workspace,
                }
            )
        mounts.extend(self._sandbox_mounts)
        if not mounts:
            return ""
        return json.dumps(
            {
                "docker": {"mounts": mounts},
                "podman": {"mounts": mounts},
            },
            separators=(",", ":"),
        )

    def _uploads(self, workspace: Path) -> list[str]:
        uploads = list(self._sandbox_uploads)
        if not self._upload_workspace:
            return uploads
        target = self._upload_workspace_target or self._sandbox_workspace
        return [f"{workspace}:{target}", *uploads]

    async def _broker_is_ready(self, info: OpenShellSessionInfo) -> bool:
        if not self._require_broker_ready or self._endpoint_exposure != "forward":
            return True
        if info.port is None:
            return False
        if self._healthcheck is not None:
            return await self._healthcheck(info.port)
        return await asyncio.to_thread(
            _http_healthcheck,
            "127.0.0.1",
            info.port,
            self._broker_health_path,
            self._broker_health_timeout,
        )

    def _chat_endpoint(self, session_id: object, port: int) -> str:
        host = os.environ.get("NIUU_SERVER_PUBLIC_HOST") or os.environ.get("NIUU_SERVER_HOST")
        host = (host or "127.0.0.1").strip() or "127.0.0.1"
        host = "localhost" if host == "127.0.0.1" else host
        server_port = os.environ.get("NIUU_SERVER_PORT", "8080")
        return f"ws://{host}:{server_port}/s/{session_id}/session"

    def _code_endpoint(self, sandbox_name: str, workspace: Path) -> str:
        if self._gateway_url:
            return f"{self._gateway_url}/sandboxes/{sandbox_name}"
        return f"file://{workspace}"

    def _sandbox_name(self, session: Session) -> str:
        return f"forge-{session.id}"

    @staticmethod
    def _runtime_from_spec(spec: SessionSpec) -> str:
        broker = spec.values.get("broker", {})
        if isinstance(broker, dict):
            return str(broker.get("cliType") or broker.get("runtime") or "skuld")
        return "skuld"

    @staticmethod
    def _map_status(obj: dict[str, Any]) -> SessionStatus:
        status_obj = obj.get("status")
        if isinstance(status_obj, dict):
            raw_value = (
                status_obj.get("state")
                or status_obj.get("phase")
                or status_obj.get("lifecycle")
                or obj.get("state")
                or obj.get("phase")
                or ""
            )
        else:
            raw_value = obj.get("state") or status_obj or obj.get("phase") or obj.get("lifecycle")
        raw = str(raw_value or "").lower()
        if raw in {"ready", "running", "active"}:
            return SessionStatus.RUNNING
        if raw in {"provisioning", "pending", "creating", "starting", "created"}:
            return SessionStatus.PROVISIONING
        if raw in {"deleting", "deleted", "stopped", "terminated", "suspended"}:
            return SessionStatus.STOPPED
        if raw in {"error", "failed", "failure"}:
            return SessionStatus.FAILED
        conditions = obj.get("conditions") or []
        if isinstance(conditions, list):
            for condition in conditions:
                if not isinstance(condition, dict):
                    continue
                if condition.get("type") != "Ready":
                    continue
                if condition.get("status") is True or condition.get("status") == "True":
                    return SessionStatus.RUNNING
        return SessionStatus.PROVISIONING

    def _persist_state(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {sid: info.to_dict() for sid, info in self._sessions.items()}
        tmp_path = self._state_file.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp_path.replace(self._state_file)

    def _load_state(self) -> None:
        if not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load OpenShell state file: %s", exc)
            return
        if not isinstance(data, dict):
            return
        for sid, raw in data.items():
            if not isinstance(raw, dict):
                continue
            info = OpenShellSessionInfo.from_dict(raw)
            if info.session_id:
                self._sessions[str(sid)] = info


def _safe_env_var(key: object, value: object) -> bool:
    if not isinstance(key, str):
        return False
    if not isinstance(value, str):
        return False
    if "\x00" in key or "\x00" in value:
        return False
    if key.startswith("SKULD__"):
        return True
    return key in {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GIT_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    }


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    raw = str(value).strip().lower()
    if raw in {"", "0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool(value)


def _normalize_command(command: list[str] | str | None) -> list[str] | None:
    if command is None:
        return None
    if isinstance(command, list):
        return command
    raw = command.strip()
    if not raw:
        return None
    if raw.startswith("["):
        loaded = json.loads(raw)
        if not isinstance(loaded, list):
            raise ValueError("sandbox_command JSON must be a list")
        return [str(item) for item in loaded]
    return shlex.split(raw)


def _normalize_string_list(value: Sequence[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            loaded = json.loads(raw)
            if not isinstance(loaded, list):
                raise ValueError("sandbox_uploads JSON must be a list")
            return [str(item) for item in loaded]
        return [raw]
    return [str(item) for item in value]


def _normalize_mounts(value: Sequence[dict[str, str] | str] | str | None) -> list[dict[str, str]]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            loaded = json.loads(raw)
            if not isinstance(loaded, list):
                raise ValueError("sandbox_mounts JSON must be a list")
            return [_mount_from_value(item) for item in loaded]
        return [_mount_from_value(part) for part in raw.split(",") if part.strip()]
    return [_mount_from_value(item) for item in value]


def _mount_from_value(value: dict[str, str] | str) -> dict[str, str]:
    if isinstance(value, dict):
        source = str(value.get("source") or "").strip()
        target = str(value.get("target") or "").strip()
    else:
        source, sep, target = str(value).partition(":")
        if not sep:
            raise ValueError("sandbox_mounts entries must be source:target")
        source = source.strip()
        target = target.strip()
    if not source or not target:
        raise ValueError("sandbox_mounts entries must include source and target")
    return {
        "type": "bind",
        "source": str(Path(source).expanduser()),
        "target": target,
    }


def _http_healthcheck(host: str, port: int, path: str, timeout: float) -> bool:
    conn: http.client.HTTPConnection | None = None
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path)
        response = conn.getresponse()
        return 200 <= response.status < 500
    except OSError:
        return False
    finally:
        if conn is not None:
            conn.close()


def _state_from_session_status(status: SessionStatus) -> OpenShellState:
    match status:
        case SessionStatus.RUNNING:
            return OpenShellState.RUNNING
        case SessionStatus.FAILED:
            return OpenShellState.FAILED
        case SessionStatus.STOPPED:
            return OpenShellState.STOPPED
    return OpenShellState.STARTING
