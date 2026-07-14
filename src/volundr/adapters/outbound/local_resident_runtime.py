"""Docker-backed resident runtime adapter for mini and local development."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shlex
import shutil
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import docker
from docker.errors import ImageNotFound, NotFound

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.resident_container_spec import (
    HERMES_API_SERVER_KEY_ENV,
    PLATFORM_ACCESS_TOKEN_ENV,
    ResidentContainerProcess,
    ResidentContainerSpec,
    materialize_resident_container,
    resident_flock_environment,
    resident_flock_labels,
    resident_flock_profile_configured,
    resident_profile_values,
    resident_runtime_section,
    runtime_processes_from_values,
)
from volundr.domain.models import (
    ResidentBackend,
    ResidentCondition,
    ResidentConditionStatus,
    ResidentDeploymentProfile,
    ResidentEndpoint,
    ResidentEngine,
    ResidentLogEntry,
    ResidentLogPage,
    ResidentObservedState,
    ResidentRuntime,
)
from volundr.domain.ports import (
    CredentialStorePort,
    ResidentDeviceApprover,
    ResidentRuntimeController,
    ResidentRuntimeLogReader,
    ResidentRuntimeObservation,
    ResidentRuntimeProxyTargetResolver,
)

logger = logging.getLogger(__name__)

DEFAULT_RESIDENTS_DIR = "~/.niuu/residents"
DEFAULT_SERVICE_PORT = 9200
DEFAULT_READY_TIMEOUT = 180.0
DEFAULT_READY_POLL_INTERVAL = 1.0
DEFAULT_STOP_TIMEOUT = 15
DEFAULT_SANDBOX_COMMAND = ("/usr/local/bin/openshell-run-installed-skuld",)
MANAGED_BY_LABEL = "volundr.niuu.io/managed-by"
RUNTIME_ID_LABEL = "volundr.niuu.io/resident"
SPEC_HASH_LABEL = "volundr.niuu.io/spec-hash"
LOG_LINE = re.compile(r"^(?P<timestamp>\S+)\s+(?:\[(?P<source>[^]]+)\]\s+)?(?P<message>.*)$")


class LocalContainerResidentRuntimeController(
    ResidentRuntimeController,
    ResidentRuntimeLogReader,
    ResidentRuntimeProxyTargetResolver,
    ResidentDeviceApprover,
):
    """Run resident images locally through the Docker Engine API."""

    def __init__(
        self,
        *,
        residents_dir: str = DEFAULT_RESIDENTS_DIR,
        host_home_dir: str = "~",
        mount_agent_credentials: bool = True,
        volundr_api_url: str = "http://host.docker.internal:8080",
        default_image: str = "",
        service_port: int = DEFAULT_SERVICE_PORT,
        service_name: str = "skuld",
        sandbox_command: list[str] | tuple[str, ...] | None = None,
        ready_timeout: float = DEFAULT_READY_TIMEOUT,
        ready_poll_interval: float = DEFAULT_READY_POLL_INTERVAL,
        stop_timeout: int = DEFAULT_STOP_TIMEOUT,
        retain_data_on_delete: bool = False,
        docker_base_url: str = "",
        **_extra: object,
    ) -> None:
        self._residents_dir = Path(residents_dir).expanduser().resolve()
        self._host_home_dir = Path(host_home_dir).expanduser().resolve()
        self._mount_agent_credentials = bool(mount_agent_credentials)
        self._volundr_api_url = volundr_api_url.rstrip("/")
        self._default_image = default_image
        self._service_port = int(service_port)
        self._service_name = service_name
        self._sandbox_command = tuple(sandbox_command or DEFAULT_SANDBOX_COMMAND)
        self._ready_timeout = float(ready_timeout)
        self._ready_poll_interval = float(ready_poll_interval)
        self._stop_timeout = int(stop_timeout)
        self._retain_data_on_delete = bool(retain_data_on_delete)
        self._credential_store: CredentialStorePort | None = None
        self._skuld_registry: Any | None = None
        self._client = (
            docker.DockerClient(base_url=docker_base_url) if docker_base_url else docker.from_env()
        )

    @property
    def backend(self) -> ResidentBackend:
        return ResidentBackend.LOCAL

    def set_credential_store(self, store: CredentialStorePort) -> None:
        self._credential_store = store

    def set_skuld_registry(self, registry: Any) -> None:
        self._skuld_registry = registry

    def supports(self, profile: ResidentDeploymentProfile) -> bool:
        if profile.backend is not ResidentBackend.LOCAL:
            return False
        try:
            values = resident_profile_values(profile.id, profile.deployment)
            if not values.get("image") and not self._default_image:
                return False
            if not resident_flock_profile_configured(profile, values):
                return False
            if profile.engine is ResidentEngine.RAVN:
                return True
            if profile.engine not in {ResidentEngine.OPENCLAW, ResidentEngine.HERMES}:
                return False
            return resident_runtime_section(values).get("processMode") == "replace" and bool(
                runtime_processes_from_values(values)
            )
        except (RuntimeError, TypeError, ValueError):
            return False

    async def deploy(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        if not self.supports(profile):
            raise RuntimeError(f"Local containers do not support resident profile {profile.id!r}")
        spec = await self._materialize(runtime, profile)
        machine_environment = await self._machine_environment(runtime)
        expected_hash = self._spec_hash(runtime, spec, machine_environment)
        container = await asyncio.to_thread(self._get_container, runtime)
        if container is not None:
            container.reload()
            current_hash = str(container.labels.get(SPEC_HASH_LABEL) or "")
            if current_hash == expected_hash:
                if container.status == "paused":
                    await asyncio.to_thread(container.unpause)
                elif container.status != "running":
                    await asyncio.to_thread(container.start)
                return await self._wait_for_ready(runtime, container, spec)
            await asyncio.to_thread(container.remove, force=True)

        root = self._runtime_root(runtime)
        await asyncio.to_thread(self._write_runtime_files, root, spec)
        await asyncio.to_thread(self._materialize_agent_home, root)
        labels = {
            MANAGED_BY_LABEL: "volundr",
            RUNTIME_ID_LABEL: str(runtime.id),
            "volundr.niuu.io/runtime": runtime.engine.value,
            SPEC_HASH_LABEL: expected_hash,
            **resident_flock_labels(runtime, prefix="volundr.niuu.io"),
        }
        environment = dict(spec.environment)
        environment.update(machine_environment)
        environment.update(resident_flock_environment(runtime))
        environment.setdefault(PLATFORM_ACCESS_TOKEN_ENV, "local-mini")
        run_kwargs: dict[str, Any] = {
            "image": spec.image,
            "name": self._container_name(runtime),
            "detach": True,
            "labels": labels,
            "environment": environment,
            "volumes": {
                str(root / "workspace"): {"bind": "/sandbox/workspace", "mode": "rw"},
                str(root / "config"): {"bind": "/sandbox/.volundr", "mode": "rw"},
                str(root / "home" / ".codex"): {
                    "bind": "/sandbox/.codex",
                    "mode": "rw",
                },
                str(root / "home" / ".claude"): {
                    "bind": "/sandbox/.claude",
                    "mode": "rw",
                },
            },
            "ports": {f"{spec.service_port}/tcp": ("127.0.0.1", None)},
            "init": True,
            "extra_hosts": {"host.docker.internal": "host-gateway"},
        }
        runtime_section = resident_runtime_section(
            resident_profile_values(profile.id, profile.deployment)
        )
        configured_entrypoint = runtime_section.get("containerEntrypoint")
        direct_process = runtime_section.get("containerCommandMode") == "direct"
        if direct_process:
            if len(spec.processes) != 1:
                raise RuntimeError("Direct resident container mode requires exactly one process")
            run_kwargs["command"] = list(spec.processes[0].command)
        else:
            run_kwargs["command"] = ["/bin/sh", "/sandbox/.volundr/run-resident.sh"]
        if isinstance(configured_entrypoint, list) and configured_entrypoint:
            run_kwargs["entrypoint"] = [str(part) for part in configured_entrypoint]
        elif not direct_process:
            run_kwargs["entrypoint"] = [
                "/bin/sh",
                "/sandbox/.volundr/run-resident.sh",
            ]
        try:
            container = await asyncio.to_thread(self._client.containers.run, **run_kwargs)
        except ImageNotFound:
            await asyncio.to_thread(self._client.images.pull, spec.image)
            container = await asyncio.to_thread(self._client.containers.run, **run_kwargs)
        try:
            return await self._wait_for_ready(runtime, container, spec)
        except Exception:
            await asyncio.to_thread(container.remove, force=True)
            raise

    async def reconcile(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        container = await asyncio.to_thread(self._get_container, runtime)
        if container is None:
            return await self.deploy(runtime, profile)
        spec = await self._materialize(runtime, profile)
        machine_environment = await self._machine_environment(runtime)
        container.reload()
        if str(container.labels.get(SPEC_HASH_LABEL) or "") != self._spec_hash(
            runtime,
            spec,
            machine_environment,
        ):
            return await self.deploy(runtime, profile)
        if runtime.desired_state.value == "suspended" and container.status == "running":
            await asyncio.to_thread(container.pause)
            container.reload()
        if runtime.desired_state.value == "running" and container.status == "paused":
            await asyncio.to_thread(container.unpause)
            container.reload()
        return self._observation(runtime, container, spec)

    async def restart(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        container = await asyncio.to_thread(self._required_container, runtime)
        spec = await self._materialize(runtime, profile)
        await asyncio.to_thread(container.restart, timeout=self._stop_timeout)
        return await self._wait_for_ready(runtime, container, spec)

    async def suspend(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        container = await asyncio.to_thread(self._required_container, runtime)
        container.reload()
        if container.status == "running":
            await asyncio.to_thread(container.pause)
        container.reload()
        return self._observation_from_backend_ref(runtime, container)

    async def resume(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        container = await asyncio.to_thread(self._required_container, runtime)
        container.reload()
        if container.status == "paused":
            await asyncio.to_thread(container.unpause)
        elif container.status != "running":
            await asyncio.to_thread(container.start)
        container.reload()
        return self._observation_from_backend_ref(runtime, container)

    async def delete(self, runtime: ResidentRuntime) -> bool:
        container = await asyncio.to_thread(self._get_container, runtime)
        existed = container is not None
        if container is not None:
            await asyncio.to_thread(container.remove, force=True)
        if not self._retain_data_on_delete:
            await asyncio.to_thread(shutil.rmtree, self._runtime_root(runtime).parent, True)
        await self._delete_machine_credentials(runtime)
        self._unregister_skuld(runtime)
        return existed

    async def logs(
        self,
        runtime: ResidentRuntime,
        *,
        lines: int,
        sources: tuple[str, ...],
        min_level: str,
    ) -> ResidentLogPage:
        container = await asyncio.to_thread(self._required_container, runtime)
        payload = await asyncio.to_thread(
            container.logs,
            stdout=True,
            stderr=True,
            timestamps=True,
            tail=lines,
        )
        entries = _parse_logs(payload.decode("utf-8", errors="replace"), sources, min_level)
        return ResidentLogPage(entries=entries[-lines:], buffer_total=len(entries))

    def resident_proxy_target(self, runtime: ResidentRuntime) -> SessionProxyTarget | None:
        port = int(runtime.backend_ref.get("host_port") or 0)
        if not port:
            return None
        return SessionProxyTarget(
            service_url=f"http://127.0.0.1:{port}",
            connect_host="127.0.0.1",
            connect_port=port,
        )

    async def approve_resident_device(
        self,
        runtime: ResidentRuntime,
        *,
        request_id: str,
        gateway_token: str,
    ) -> None:
        container = await asyncio.to_thread(self._required_container, runtime)
        result = await asyncio.to_thread(
            container.exec_run,
            ["openclaw", "devices", "approve", request_id],
            environment={
                "OPENCLAW_GATEWAY_TOKEN": gateway_token,
                "OPENCLAW_STATE_DIR": "/sandbox/workspace/.openclaw",
            },
        )
        if result.exit_code != 0:
            output = result.output.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"OpenClaw device pairing failed: {output}")

    async def close(self) -> None:
        await asyncio.to_thread(self._client.close)

    async def _materialize(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentContainerSpec:
        values = resident_profile_values(profile.id, profile.deployment)
        return materialize_resident_container(
            runtime,
            values,
            default_image=self._default_image,
            default_service_name=self._service_name,
            default_service_port=self._service_port,
            volundr_api_url=self._volundr_api_url,
            sandbox_command=self._sandbox_command,
        )

    async def _machine_environment(self, runtime: ResidentRuntime) -> dict[str, str]:
        if runtime.engine is ResidentEngine.RAVN:
            return {}
        if self._credential_store is None:
            raise RuntimeError(f"{runtime.engine.value} residents require a credential store")
        if runtime.engine is ResidentEngine.OPENCLAW:
            from volundr.adapters.outbound.openclaw_gateway import (
                ensure_openclaw_machine_credential,
            )

            credential = await ensure_openclaw_machine_credential(self._credential_store, runtime)
            return {
                "OPENCLAW_GATEWAY_TOKEN": credential["gateway_token"],
                "OPENCLAW_STATE_DIR": "/sandbox/workspace/.openclaw",
            }
        from volundr.adapters.outbound.hermes_gateway import ensure_hermes_api_key

        return {
            HERMES_API_SERVER_KEY_ENV: await ensure_hermes_api_key(self._credential_store, runtime)
        }

    async def _delete_machine_credentials(self, runtime: ResidentRuntime) -> None:
        if self._credential_store is None:
            return
        if runtime.engine is ResidentEngine.OPENCLAW:
            await self._credential_store.delete("resident", str(runtime.id), "openclaw-gateway")
        if runtime.engine is ResidentEngine.HERMES:
            from volundr.adapters.outbound.hermes_gateway import HERMES_CREDENTIAL_NAME

            await self._credential_store.delete("resident", str(runtime.id), HERMES_CREDENTIAL_NAME)

    async def _wait_for_ready(
        self,
        runtime: ResidentRuntime,
        container: Any,
        spec: ResidentContainerSpec,
    ) -> ResidentRuntimeObservation:
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            container.reload()
            if container.status in {"exited", "dead"}:
                logs = container.logs(tail=100).decode("utf-8", errors="replace")
                raise RuntimeError(f"Resident container exited before readiness:\n{logs}")
            port = _published_port(container, spec.service_port)
            if port and await asyncio.to_thread(_service_ready, port):
                return self._observation(runtime, container, spec)
            await asyncio.sleep(self._ready_poll_interval)
        raise TimeoutError(f"Resident container was not ready within {self._ready_timeout}s")

    def _observation(
        self,
        runtime: ResidentRuntime,
        container: Any,
        spec: ResidentContainerSpec,
    ) -> ResidentRuntimeObservation:
        host_port = _published_port(container, spec.service_port)
        return self._build_observation(
            runtime,
            container,
            service_name=spec.service_name,
            service_port=spec.service_port,
            host_port=host_port,
            process_names=[process.name for process in spec.processes],
        )

    def _observation_from_backend_ref(
        self,
        runtime: ResidentRuntime,
        container: Any,
    ) -> ResidentRuntimeObservation:
        return self._build_observation(
            runtime,
            container,
            service_name=str(runtime.backend_ref.get("service_name") or self._service_name),
            service_port=int(runtime.backend_ref.get("service_port") or self._service_port),
            host_port=int(runtime.backend_ref.get("host_port") or 0),
            process_names=list(runtime.backend_ref.get("process_names") or []),
        )

    def _build_observation(
        self,
        runtime: ResidentRuntime,
        container: Any,
        *,
        service_name: str,
        service_port: int,
        host_port: int,
        process_names: list[str],
    ) -> ResidentRuntimeObservation:
        container.reload()
        state = _observed_state(container.status)
        ready = state is ResidentObservedState.ACTIVE and bool(host_port)
        endpoints: list[ResidentEndpoint] = []
        if ready and runtime.engine is ResidentEngine.RAVN:
            endpoints.append(
                ResidentEndpoint(
                    kind="chat",
                    protocol="skuld-v1",
                    url=f"/s/{runtime.id}/session",
                )
            )
        elif ready:
            protocol = (
                "openclaw-gateway-v4"
                if runtime.engine is ResidentEngine.OPENCLAW
                else "hermes-api-server-v1"
            )
            endpoints.append(
                ResidentEndpoint(
                    kind="sessions",
                    protocol=protocol,
                    url=f"/api/v1/forge/resident-runtimes/{runtime.id}/sessions",
                )
            )
        observation = ResidentRuntimeObservation(
            observed_state=state,
            backend_ref={
                "kind": "DockerContainer",
                "id": container.id,
                "name": container.name,
                "service_name": service_name,
                "service_port": service_port,
                "host_port": host_port,
                "process_names": process_names,
            },
            endpoints=endpoints,
            conditions=[
                ResidentCondition(
                    type="ContainerReady",
                    status=(
                        ResidentConditionStatus.TRUE if ready else ResidentConditionStatus.FALSE
                    ),
                    reason="Ready" if ready else container.status.title(),
                )
            ],
        )
        self._sync_skuld_registration(runtime, observation)
        return observation

    def _sync_skuld_registration(
        self,
        runtime: ResidentRuntime,
        observation: ResidentRuntimeObservation,
    ) -> None:
        if self._skuld_registry is None or runtime.engine is not ResidentEngine.RAVN:
            return
        host_port = int(observation.backend_ref.get("host_port") or 0)
        if observation.observed_state is ResidentObservedState.ACTIVE and host_port:
            self._skuld_registry.register(str(runtime.id), host_port)
        else:
            self._unregister_skuld(runtime)

    def _unregister_skuld(self, runtime: ResidentRuntime) -> None:
        if self._skuld_registry is not None and runtime.engine is ResidentEngine.RAVN:
            self._skuld_registry.unregister(str(runtime.id))

    def _get_container(self, runtime: ResidentRuntime) -> Any | None:
        try:
            return self._client.containers.get(self._container_name(runtime))
        except NotFound:
            return None

    def _required_container(self, runtime: ResidentRuntime) -> Any:
        container = self._get_container(runtime)
        if container is None:
            raise RuntimeError(f"Resident container does not exist: {runtime.id}")
        return container

    def _runtime_root(self, runtime: ResidentRuntime) -> Path:
        return self._residents_dir / str(runtime.id) / "sandbox"

    def _materialize_agent_home(self, root: Path) -> None:
        if not self._mount_agent_credentials:
            return
        candidates = {
            self._host_home_dir / ".codex" / "auth.json": root / "home" / ".codex" / "auth.json",
            self._host_home_dir / ".codex" / "config.toml": root
            / "home"
            / ".codex"
            / "config.toml",
            self._host_home_dir / ".claude" / ".credentials.json": root
            / "home"
            / ".claude"
            / ".credentials.json",
        }
        for source, destination in candidates.items():
            if not source.is_file() or destination.exists():
                continue
            shutil.copy2(source, destination)
            destination.chmod(0o600)

    @staticmethod
    def _container_name(runtime: ResidentRuntime) -> str:
        return f"volundr-resident-{runtime.id.hex[:22]}"

    @staticmethod
    def _spec_hash(
        runtime: ResidentRuntime,
        spec: ResidentContainerSpec,
        machine_environment: dict[str, str] | None = None,
    ) -> str:
        payload = {
            "runtime": {
                "engine": runtime.engine.value,
                "model": runtime.model,
                "persona": runtime.persona_name,
            },
            "spec": {
                "image": spec.image,
                "service_name": spec.service_name,
                "service_port": spec.service_port,
                "environment": spec.environment,
                "files": {
                    path: hashlib.sha256(content).hexdigest()
                    for path, content in spec.files.items()
                },
                "processes": [
                    {
                        "name": process.name,
                        "command": process.command,
                        "env": process.env,
                        "log_path": process.log_path,
                    }
                    for process in spec.processes
                ],
            },
        }
        if machine_environment:
            payload["machine_environment"] = machine_environment
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]

    @staticmethod
    def _write_runtime_files(root: Path, spec: ResidentContainerSpec) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "workspace").mkdir(parents=True, exist_ok=True)
        (root / "home" / ".codex").mkdir(parents=True, exist_ok=True)
        (root / "home" / ".claude").mkdir(parents=True, exist_ok=True)
        runtime_dir = root / "config"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        for destination, content in spec.files.items():
            path = _host_runtime_path(root, destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(0o600)
        script = _supervisor_script(spec.processes)
        script_path = runtime_dir / "run-resident.sh"
        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o700)


def _host_runtime_path(root: Path, destination: str) -> Path:
    mappings = {
        "/sandbox/workspace/": root / "workspace",
        "/sandbox/.volundr/": root / "config",
        "/sandbox/.codex/": root / "home" / ".codex",
        "/sandbox/.claude/": root / "home" / ".claude",
    }
    for prefix, host_root in mappings.items():
        if destination.startswith(prefix):
            return host_root / destination.removeprefix(prefix)
    raise RuntimeError(f"Resident file path is not backed by durable local storage: {destination}")


def _supervisor_script(processes: tuple[ResidentContainerProcess, ...]) -> str:
    lines = [
        "#!/bin/sh",
        "set -eu",
        "pids=''",
        "terminate() {",
        '  [ -z "$pids" ] || kill $pids 2>/dev/null || true',
        "  wait $pids 2>/dev/null || true",
        "}",
        "trap terminate INT TERM EXIT",
    ]
    for process in processes:
        env = " ".join(
            f"{name}={shlex.quote(value)}" for name, value in sorted(process.env.items())
        )
        command = " ".join(shlex.quote(part) for part in process.command)
        invocation = f"env {env} {command}" if env else command
        log_path = shlex.quote(process.log_path)
        source = f"[{process.name}] "
        lines.extend(
            [
                f"mkdir -p $(dirname {log_path})",
                f'({invocation}) 2>&1 | tee -a {log_path} | sed -u "s/^/{source}/" &',
                'pids="$pids $!"',
            ]
        )
    lines.extend(
        [
            "while :; do",
            "  for pid in $pids; do",
            "    kill -0 $pid 2>/dev/null || exit 1",
            "  done",
            "  sleep 1",
            "done",
            "",
        ]
    )
    return "\n".join(lines)


def _published_port(container: Any, service_port: int) -> int:
    bindings = (container.attrs.get("NetworkSettings") or {}).get("Ports") or {}
    rows = bindings.get(f"{service_port}/tcp") or []
    if not rows:
        return 0
    return int(rows[0].get("HostPort") or 0)


def _service_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5) as connection:
            connection.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            return connection.recv(16).startswith(b"HTTP/")
    except OSError:
        return False


def _observed_state(status: str) -> ResidentObservedState:
    if status == "running":
        return ResidentObservedState.ACTIVE
    if status == "paused":
        return ResidentObservedState.SUSPENDED
    if status in {"created", "restarting"}:
        return ResidentObservedState.DEPLOYING
    if status in {"exited", "dead"}:
        return ResidentObservedState.FAILED
    return ResidentObservedState.PENDING


def _parse_logs(
    payload: str,
    sources: tuple[str, ...],
    min_level: str,
) -> list[ResidentLogEntry]:
    levels = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}
    minimum = levels.get(min_level.lower(), 0)
    entries: list[ResidentLogEntry] = []
    for line in payload.splitlines():
        match = LOG_LINE.match(line)
        if match is None:
            continue
        source = match.group("source") or "container"
        if sources and source not in sources:
            continue
        message = match.group("message")
        level = _log_level(message)
        if levels.get(level.lower(), 20) < minimum:
            continue
        try:
            timestamp = datetime.fromisoformat(match.group("timestamp").replace("Z", "+00:00"))
        except ValueError:
            timestamp = datetime.now(UTC)
        entries.append(
            ResidentLogEntry(
                timestamp_ms=int(timestamp.timestamp() * 1000),
                level=level,
                source=source,
                target=source,
                message=message,
            )
        )
    return entries


def _log_level(message: str) -> str:
    lowered = message.lower()
    if "critical" in lowered or "fatal" in lowered:
        return "critical"
    if "error" in lowered or "exception" in lowered:
        return "error"
    if "warning" in lowered or "warn" in lowered:
        return "warning"
    if "debug" in lowered:
        return "debug"
    return "info"
