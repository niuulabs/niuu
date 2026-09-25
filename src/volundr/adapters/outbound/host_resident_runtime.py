"""Host-process resident runtime: Ravn residents without a container engine.

Runs a resident's Skuld broker and Ravn daemon as child processes of the
platform. Processes, files and environment come from the same materialization
the container runtime uses. ``/sandbox`` paths are translated onto the
resident's durable directory under ``residents_dir`` (the layout the Docker
runtime mounts), and every listener moves to loopback: the Skuld service and
the Ravn HTTP channel on OS-assigned ports, the mesh onto per-resident IPC
sockets.

The processes live as long as the platform: ``close()`` stops them, and a
process record left behind by a platform that died is used to stop the
orphans before a resident starts again, so one resident never runs twice.

Constructor accepts plain kwargs (dynamic adapter pattern):
    adapter: "volundr.adapters.outbound.host_resident_runtime.HostProcessResidentRuntimeController"
    residents_dir: "~/.niuu/residents"
    volundr_api_url: "http://127.0.0.1:8080"
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID

import yaml

from niuu.mesh.ipc import cleanup_ipc_socket_dir, flock_socket_dir, ipc_address
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.local_resident_storage import (
    LOCAL_PLATFORM_ACCESS_TOKEN,
    host_runtime_path,
    materialize_agent_home,
    parse_resident_logs,
    service_ready,
    write_resident_files,
)
from volundr.adapters.outbound.resident_container_spec import (
    PLATFORM_ACCESS_TOKEN_ENV,
    RESIDENT_RAVN_CONFIG,
    RESIDENT_SKULD_CONFIG,
    SANDBOX_HOME,
    ResidentContainerProcess,
    materialize_resident_runtime,
    resident_flock_environment,
    resident_flock_profile_configured,
    resident_process_log_path,
    resident_profile_values,
    resident_ravn_daemon_arguments,
    resident_runtime_section,
    runtime_processes_from_values,
)
from volundr.domain.models import (
    ResidentBackend,
    ResidentCondition,
    ResidentConditionStatus,
    ResidentDeploymentProfile,
    ResidentDesiredState,
    ResidentEndpoint,
    ResidentEngine,
    ResidentLogPage,
    ResidentObservedState,
    ResidentRuntime,
)
from volundr.domain.ports import (
    ResidentRuntimeController,
    ResidentRuntimeLogReader,
    ResidentRuntimeObservation,
    ResidentRuntimeProxyTargetResolver,
)

logger = logging.getLogger(__name__)

DEFAULT_RESIDENTS_DIR = "~/.niuu/residents"
DEFAULT_VOLUNDR_API_URL = "http://127.0.0.1:8080"
DEFAULT_SERVICE_NAME = "skuld"
DEFAULT_READY_TIMEOUT = 180.0
DEFAULT_READY_POLL_INTERVAL = 1.0
DEFAULT_STOP_TIMEOUT = 15.0
# Process-bootstrap variables a resident inherits from the platform. Everything
# else comes from its materialized spec, as it would inside a container, so the
# platform's own configuration (SKULD__*, RAVN_*, NIUU_*) never leaks into it.
DEFAULT_INHERITED_ENVIRONMENT = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)
HOST_PROCESS_NAMES = ("skuld", "ravn")
BACKEND_REF_KIND = "HostProcesses"
CONDITION_TYPE = "ProcessesReady"
STATE_FILE_NAME = "host-processes.json"
LOOPBACK_HOST = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"
RAVN_PLATFORM_TOKEN_ENV = "RAVN__GATEWAY__PLATFORM__PAT_TOKEN"
LOG_CHUNK_BYTES = 65536
FAILURE_LOG_LINES = 100
SANDBOX_REFERENCE = re.compile(r"(?<![\w./-])/sandbox(?![\w.-])")


@dataclass
class _HostProcess:
    """One running resident process and the task recording its output."""

    name: str
    command: tuple[str, ...]
    log_path: Path
    process: asyncio.subprocess.Process
    output: asyncio.Task[None]
    # Set once its process group was signalled after exit; the group id may be
    # reused by an unrelated process later, so it is never signalled again.
    stopped: bool = False


@dataclass
class _HostResident:
    """Processes started for one resident from one desired spec."""

    spec_hash: str
    service_name: str
    service_port: int
    processes: list[_HostProcess]
    failure: str = ""


@dataclass(frozen=True)
class _HostLayout:
    """Translate a ``/sandbox`` resident spec onto one resident's host directory."""

    root: Path
    ravn_http_port: int

    def path(self, value: str) -> str:
        if value == SANDBOX_HOME:
            return str(self.root / "home")
        return str(host_runtime_path(self.root, value))

    def translate(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.translate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.translate(item) for item in value]
        if not isinstance(value, str) or SANDBOX_REFERENCE.search(value) is None:
            return value
        if value == SANDBOX_HOME or value.startswith(f"{SANDBOX_HOME}/"):
            return self.path(value)
        raise RuntimeError(
            f"Resident setting embeds a container path host processes cannot map: {value!r}"
        )

    def environment(self, environment: dict[str, str]) -> dict[str, str]:
        translated = {name: self.translate(value) for name, value in environment.items()}
        translated["SKULD__HOST"] = LOOPBACK_HOST
        _require_loopback(translated)
        return translated

    def config_file(self, destination: str, content: bytes) -> bytes:
        config = yaml.safe_load(content)
        if not isinstance(config, dict):
            raise RuntimeError(f"Resident file {destination!r} is not a configuration mapping")
        config = self.translate(config)
        if destination == RESIDENT_SKULD_CONFIG:
            config["host"] = LOOPBACK_HOST
            self._rebind_mesh(config, "skuld")
        elif destination == RESIDENT_RAVN_CONFIG:
            self._rebind_mesh(config, "ravn")
            http = _section(config, "gateway", "channels", "http")
            if http is not None:
                http["host"] = LOOPBACK_HOST
                http["port"] = self.ravn_http_port
        else:
            raise RuntimeError(f"Host processes cannot place resident file {destination!r}")
        _require_loopback(config)
        return yaml.safe_dump(config, sort_keys=False).encode()

    def processes(
        self,
        processes: tuple[ResidentContainerProcess, ...],
        runtime: ResidentRuntime,
        entrypoint: tuple[str, ...],
        *,
        access_token: str,
    ) -> tuple[ResidentContainerProcess, ...]:
        by_name = {process.name: process for process in processes}
        if set(by_name) != set(HOST_PROCESS_NAMES) or len(processes) != len(HOST_PROCESS_NAMES):
            raise RuntimeError(
                "Host processes run the built-in Skuld and Ravn resident processes only, "
                f"not {sorted(by_name)}"
            )
        skuld = by_name["skuld"]
        ravn = by_name["ravn"]
        daemon = resident_ravn_daemon_arguments(runtime, self.path(RESIDENT_RAVN_CONFIG))
        translated = (
            ResidentContainerProcess(
                name=skuld.name,
                command=skuld.command,
                env=self.translate(skuld.env),
                files={},
                log_path=self.path(skuld.log_path),
            ),
            ResidentContainerProcess(
                name=ravn.name,
                command=(*entrypoint, "ravn", *daemon),
                env={**self.translate(ravn.env), RAVN_PLATFORM_TOKEN_ENV: access_token},
                files={},
                log_path=self.path(ravn.log_path),
            ),
        )
        for process in translated:
            _require_loopback([*process.command, *process.env.values()])
        return translated

    def _rebind_mesh(self, config: dict[str, Any], peer: str) -> None:
        nng = _section(config, "mesh", "nng")
        if nng is None:
            return
        sockets = flock_socket_dir(self.root)
        nng["pub_sub_address"] = ipc_address(sockets / f"{peer}-pub.sock")
        nng["req_rep_address"] = ipc_address(sockets / f"{peer}-rep.sock")


class HostProcessResidentRuntimeController(
    ResidentRuntimeController,
    ResidentRuntimeLogReader,
    ResidentRuntimeProxyTargetResolver,
):
    """Run Ravn residents as Skuld and Ravn processes on this host."""

    def __init__(
        self,
        *,
        residents_dir: str = DEFAULT_RESIDENTS_DIR,
        host_home_dir: str = "~",
        mount_agent_credentials: bool = True,
        volundr_api_url: str = DEFAULT_VOLUNDR_API_URL,
        service_name: str = DEFAULT_SERVICE_NAME,
        ready_timeout: float = DEFAULT_READY_TIMEOUT,
        ready_poll_interval: float = DEFAULT_READY_POLL_INTERVAL,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT,
        retain_data_on_delete: bool = False,
        inherited_environment: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self._residents_dir = Path(residents_dir).expanduser().resolve()
        self._host_home_dir = Path(host_home_dir).expanduser().resolve()
        self._mount_agent_credentials = bool(mount_agent_credentials)
        self._volundr_api_url = volundr_api_url.rstrip("/")
        self._service_name = service_name
        self._ready_timeout = float(ready_timeout)
        self._ready_poll_interval = float(ready_poll_interval)
        self._stop_timeout = float(stop_timeout)
        self._retain_data_on_delete = bool(retain_data_on_delete)
        if inherited_environment is None:
            inherited_environment = DEFAULT_INHERITED_ENVIRONMENT
        self._inherited_environment = tuple(inherited_environment)
        self._entrypoint = _platform_entrypoint()
        self._skuld_registry: Any | None = None
        self._residents: dict[UUID, _HostResident] = {}
        self._profiles: dict[UUID, ResidentDeploymentProfile] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    @property
    def backend(self) -> ResidentBackend:
        return ResidentBackend.LOCAL

    def set_skuld_registry(self, registry: Any) -> None:
        self._skuld_registry = registry

    def supports(self, profile: ResidentDeploymentProfile) -> bool:
        if profile.backend is not ResidentBackend.LOCAL:
            return False
        if profile.engine is not ResidentEngine.RAVN:
            return False
        try:
            values = resident_profile_values(profile.id, profile.deployment)
            if resident_runtime_section(values).get("processMode") == "replace":
                return False
            if runtime_processes_from_values(values):
                return False
            return resident_flock_profile_configured(profile, values)
        except (RuntimeError, TypeError, ValueError):
            return False

    async def deploy(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        async with self._lock(runtime.id):
            return await self._converge(runtime, profile)

    async def reconcile(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        async with self._lock(runtime.id):
            self._profiles[runtime.id] = profile
            if runtime.desired_state is ResidentDesiredState.SUSPENDED:
                await self._stop(runtime)
                return self._idle_observation(runtime, ResidentObservedState.SUSPENDED, "Suspended")
            resident = self._residents.get(runtime.id)
            if resident is None or resident.spec_hash != self._spec_hash(runtime, profile):
                return await self._converge(runtime, profile)
            if not resident.failure and (failure := _failure(resident)):
                await self._terminate(resident.processes)
                resident.failure = failure
            return self._observation(runtime, resident)

    async def restart(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        async with self._lock(runtime.id):
            await self._stop(runtime)
            return await self._converge(runtime, profile)

    async def suspend(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        async with self._lock(runtime.id):
            await self._stop(runtime)
            return self._idle_observation(runtime, ResidentObservedState.SUSPENDED, "Suspended")

    async def resume(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        async with self._lock(runtime.id):
            profile = self._profiles.get(runtime.id)
            if profile is None:
                return self._idle_observation(
                    runtime,
                    ResidentObservedState.DEPLOYING,
                    "AwaitingReconcile",
                    "The platform started after this resident was suspended; the next "
                    "reconciliation starts its processes.",
                )
            return await self._converge(runtime, profile)

    async def delete(self, runtime: ResidentRuntime) -> bool:
        root = self._runtime_root(runtime)
        async with self._lock(runtime.id):
            existed = runtime.id in self._residents or root.parent.exists()
            await self._stop(runtime)
            self._profiles.pop(runtime.id, None)
            await asyncio.to_thread(_remove_tree, flock_socket_dir(root))
            if not self._retain_data_on_delete:
                await asyncio.to_thread(_remove_tree, root.parent)
        self._locks.pop(runtime.id, None)
        return existed

    async def logs(
        self,
        runtime: ResidentRuntime,
        *,
        lines: int,
        sources: tuple[str, ...],
        min_level: str,
    ) -> ResidentLogPage:
        root = self._runtime_root(runtime)
        paths = [
            host_runtime_path(root, resident_process_log_path(str(name)))
            for name in runtime.backend_ref.get("process_names") or ()
        ]
        payload = await asyncio.to_thread(_tail_logs, paths, lines)
        entries = sorted(
            parse_resident_logs(payload, sources, min_level),
            key=lambda entry: entry.timestamp_ms,
        )
        return ResidentLogPage(entries=entries[-lines:], buffer_total=len(entries))

    def resident_proxy_target(self, runtime: ResidentRuntime) -> SessionProxyTarget | None:
        port = int(runtime.backend_ref.get("host_port") or 0)
        if not port:
            return None
        return SessionProxyTarget(
            service_url=f"http://{LOOPBACK_HOST}:{port}",
            connect_host=LOOPBACK_HOST,
            connect_port=port,
        )

    async def close(self) -> None:
        residents = list(self._residents.values())
        self._residents.clear()
        for resident in residents:
            await self._terminate(resident.processes)

    async def _converge(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        if not self.supports(profile):
            raise RuntimeError(
                f"Host processes cannot run resident profile {profile.id!r}: only local Ravn "
                "profiles without replacement or additional processes run without a container "
                "engine. Configure a container resident runtime for this profile."
            )
        self._profiles[runtime.id] = profile
        spec_hash = self._spec_hash(runtime, profile)
        resident = self._residents.get(runtime.id)
        if (
            resident is None
            or resident.spec_hash != spec_hash
            or resident.failure
            or _failure(resident)
        ):
            await self._stop(runtime)
            resident = await self._start(runtime, profile, spec_hash)
        try:
            await self._wait_for_ready(runtime, resident)
        except BaseException:
            await self._stop(runtime)
            raise
        return self._observation(runtime, resident)

    async def _start(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        spec_hash: str,
    ) -> _HostResident:
        root = self._runtime_root(runtime)
        service_port, ravn_http_port = _reserve_loopback_ports(2)
        values = _with_service_port(
            resident_profile_values(profile.id, profile.deployment),
            service_port,
        )
        spec = materialize_resident_runtime(
            runtime,
            values,
            default_service_name=self._service_name,
            default_service_port=service_port,
            volundr_api_url=self._volundr_api_url,
            sandbox_command=(*self._entrypoint, "skuld"),
        )
        layout = _HostLayout(root=root, ravn_http_port=ravn_http_port)
        files = {
            destination: layout.config_file(destination, content)
            for destination, content in spec.files.items()
        }
        environment = self._process_environment(runtime, layout.environment(spec.environment))
        processes = layout.processes(
            spec.processes,
            runtime,
            self._entrypoint,
            access_token=environment[PLATFORM_ACCESS_TOKEN_ENV],
        )
        await asyncio.to_thread(self._prepare_root, root, files)

        started: list[_HostProcess] = []
        try:
            for process in processes:
                started.append(await self._spawn(root, process, environment))
                # Recorded per process, so a platform that dies mid-start leaves
                # no orphan the next start cannot find.
                await asyncio.to_thread(self._record_processes, runtime, started)
        except BaseException:
            await self._terminate(started)
            raise
        resident = _HostResident(
            spec_hash=spec_hash,
            service_name=spec.service_name,
            service_port=spec.service_port,
            processes=started,
        )
        self._residents[runtime.id] = resident
        return resident

    async def _spawn(
        self,
        root: Path,
        process: ResidentContainerProcess,
        environment: dict[str, str],
    ) -> _HostProcess:
        handle = await asyncio.create_subprocess_exec(
            *process.command,
            cwd=str(root / "home"),
            env={**environment, **process.env},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # Its own process group, so stopping the resident also stops the
            # agent CLIs it starts.
            start_new_session=True,
        )
        if handle.stdout is None:
            raise RuntimeError(f"Resident process {process.name} has no output pipe")
        log_path = Path(process.log_path)
        output = asyncio.create_task(
            _record_output(handle.stdout, log_path, process.name),
            name=f"resident-output-{process.name}-{handle.pid}",
        )
        logger.info("Started resident process %s pid=%d", process.name, handle.pid)
        return _HostProcess(
            name=process.name,
            command=process.command,
            log_path=log_path,
            process=handle,
            output=output,
        )

    async def _wait_for_ready(self, runtime: ResidentRuntime, resident: _HostResident) -> None:
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            if failure := _failure(resident):
                raise RuntimeError(
                    f"Resident {runtime.id} {failure} before readiness:\n"
                    f"{await self._failure_log(resident)}"
                )
            if await asyncio.to_thread(service_ready, resident.service_port):
                return
            await asyncio.sleep(self._ready_poll_interval)
        raise TimeoutError(
            f"Resident {runtime.id} processes were not ready within {self._ready_timeout}s"
        )

    async def _failure_log(self, resident: _HostResident) -> str:
        exited = [item for item in resident.processes if item.process.returncode is not None]
        pending = [item.output for item in exited if not item.output.done()]
        if pending:
            await asyncio.wait(pending, timeout=self._ready_poll_interval)
        return await asyncio.to_thread(
            _tail_logs,
            [item.log_path for item in exited],
            FAILURE_LOG_LINES,
        )

    async def _stop(self, runtime: ResidentRuntime) -> None:
        resident = self._residents.pop(runtime.id, None)
        if resident is not None:
            await self._terminate(resident.processes)
        tracked = {
            item.process.pid for current in self._residents.values() for item in current.processes
        }
        await asyncio.to_thread(self._terminate_recorded, runtime, tracked)
        self._unregister_skuld(runtime)

    async def _terminate(self, processes: list[_HostProcess]) -> None:
        active = [item for item in processes if not item.stopped]
        running = [item for item in active if item.process.returncode is None]
        for item in running:
            _signal_group(item.process.pid, signal.SIGTERM)
        if running:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(item.process.wait() for item in running)),
                    timeout=self._stop_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "Resident processes ignored SIGTERM for %.1fs; killing them",
                    self._stop_timeout,
                )
        for item in active:
            # Also reaches descendants left in the group after its leader exited.
            _signal_group(item.process.pid, signal.SIGKILL)
            await item.process.wait()
            item.stopped = True
        outputs = [item.output for item in active if not item.output.done()]
        if outputs:
            _, unfinished = await asyncio.wait(outputs, timeout=self._stop_timeout)
            for task in unfinished:
                logger.warning(
                    "A descendant of resident process %s left its process group and still "
                    "holds its output; it keeps running unrecorded",
                    task.get_name(),
                )
                task.cancel()

    def _terminate_recorded(self, runtime: ResidentRuntime, tracked: set[int]) -> None:
        """Stop processes a previous platform run started for this resident."""
        path = self._state_path(runtime)
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Resident process record {path} is unreadable ({exc}). Stop any leftover "
                f"skuld and ravn processes of resident {runtime.id}, then delete the file."
            ) from exc
        for entry in recorded.get("processes") or []:
            pid = int(entry["pid"])
            command = " ".join(str(part) for part in entry["command"])
            if pid in tracked or not _process_alive(pid):
                continue
            if not _command_line(pid, self._stop_timeout).startswith(command):
                continue
            logger.warning("Stopping orphaned resident process pid=%d (%s)", pid, command)
            _signal_group(pid, signal.SIGTERM)
            deadline = time.monotonic() + self._stop_timeout
            while time.monotonic() < deadline and _process_alive(pid):
                time.sleep(self._ready_poll_interval)
            _signal_group(pid, signal.SIGKILL)
        path.unlink(missing_ok=True)

    def _record_processes(self, runtime: ResidentRuntime, processes: list[_HostProcess]) -> None:
        path = self._state_path(runtime)
        path.write_text(
            json.dumps(
                {
                    "processes": [
                        {"name": item.name, "pid": item.process.pid, "command": list(item.command)}
                        for item in processes
                    ]
                }
            ),
            encoding="utf-8",
        )

    def _prepare_root(self, root: Path, files: dict[str, bytes]) -> None:
        write_resident_files(root, files)
        if self._mount_agent_credentials:
            materialize_agent_home(root, self._host_home_dir)
        flock_socket_dir(root).mkdir(parents=True, exist_ok=True)
        cleanup_ipc_socket_dir(root)

    def _process_environment(
        self,
        runtime: ResidentRuntime,
        spec_environment: dict[str, str],
    ) -> dict[str, str]:
        environment = {
            name: os.environ[name] for name in self._inherited_environment if name in os.environ
        }
        environment.update(spec_environment)
        environment.update(resident_flock_environment(runtime))
        environment.setdefault(PLATFORM_ACCESS_TOKEN_ENV, LOCAL_PLATFORM_ACCESS_TOKEN)
        return environment

    def _observation(
        self,
        runtime: ResidentRuntime,
        resident: _HostResident,
    ) -> ResidentRuntimeObservation:
        failure = resident.failure or _failure(resident)
        observation = ResidentRuntimeObservation(
            observed_state=(
                ResidentObservedState.FAILED if failure else ResidentObservedState.ACTIVE
            ),
            backend_ref={
                "kind": BACKEND_REF_KIND,
                "service_name": resident.service_name,
                "service_port": resident.service_port,
                "host_port": 0 if failure else resident.service_port,
                "process_names": [item.name for item in resident.processes],
                "pids": {item.name: item.process.pid for item in resident.processes},
            },
            endpoints=(
                []
                if failure
                else [
                    ResidentEndpoint(
                        kind="chat",
                        protocol="skuld-v1",
                        url=f"/s/{runtime.id}/session",
                    )
                ]
            ),
            conditions=[
                ResidentCondition(
                    type=CONDITION_TYPE,
                    status=(
                        ResidentConditionStatus.FALSE if failure else ResidentConditionStatus.TRUE
                    ),
                    reason="ProcessExited" if failure else "Ready",
                    message=failure,
                )
            ],
        )
        if failure:
            self._unregister_skuld(runtime)
        elif self._skuld_registry is not None:
            self._skuld_registry.register(str(runtime.id), resident.service_port)
        return observation

    def _idle_observation(
        self,
        runtime: ResidentRuntime,
        state: ResidentObservedState,
        reason: str,
        message: str = "",
    ) -> ResidentRuntimeObservation:
        self._unregister_skuld(runtime)
        return ResidentRuntimeObservation(
            observed_state=state,
            backend_ref={
                "kind": BACKEND_REF_KIND,
                "service_name": self._service_name,
                "service_port": 0,
                "host_port": 0,
                "process_names": list(HOST_PROCESS_NAMES),
            },
            conditions=[
                ResidentCondition(
                    type=CONDITION_TYPE,
                    status=ResidentConditionStatus.FALSE,
                    reason=reason,
                    message=message,
                )
            ],
        )

    def _unregister_skuld(self, runtime: ResidentRuntime) -> None:
        if self._skuld_registry is not None:
            self._skuld_registry.unregister(str(runtime.id))

    def _spec_hash(self, runtime: ResidentRuntime, profile: ResidentDeploymentProfile) -> str:
        payload = {
            "runtime": runtime.model_dump(
                mode="json",
                include={
                    "name",
                    "owner_id",
                    "tenant_id",
                    "persona_name",
                    "model",
                    "engine",
                    "flock_id",
                    "flock_member_id",
                    "flock_role",
                    "flock_peer_id",
                },
            ),
            "values": resident_profile_values(profile.id, profile.deployment),
            "volundr_api_url": self._volundr_api_url,
            "service_name": self._service_name,
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(encoded).hexdigest()[:24]

    def _lock(self, runtime_id: UUID) -> asyncio.Lock:
        return self._locks.setdefault(runtime_id, asyncio.Lock())

    def _runtime_root(self, runtime: ResidentRuntime) -> Path:
        return self._residents_dir / str(runtime.id) / "sandbox"

    def _state_path(self, runtime: ResidentRuntime) -> Path:
        return self._residents_dir / str(runtime.id) / STATE_FILE_NAME


def _platform_entrypoint() -> tuple[str, ...]:
    """Command prefix that runs a platform package (skuld, ravn) as a host process."""
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return (sys.executable, "platform")
    missing = [name for name in HOST_PROCESS_NAMES if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError(
            f"Host-process residents need the {', '.join(missing)} package(s) importable by "
            f"{sys.executable}. Install the full niuu distribution, or configure a container "
            "resident runtime instead."
        )
    return (sys.executable, "-m")


def _with_service_port(values: dict[str, Any], port: int) -> dict[str, Any]:
    """Copy profile values with the resident service moved to ``port``."""
    copied = copy.deepcopy(values)
    # The same section resident_runtime_section() reads: "runtime", else "openshell".
    key = "runtime"
    if not isinstance(copied.get(key), dict) and isinstance(copied.get("openshell"), dict):
        key = "openshell"
    section = copied[key] if isinstance(copied.get(key), dict) else {}
    service = section.get("service") if isinstance(section.get("service"), dict) else {}
    copied[key] = {**section, "service": {**service, "port": port}}
    return copied


def _reserve_loopback_ports(count: int) -> list[int]:
    """Return ``count`` distinct loopback ports the OS currently considers free."""
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(probe)
            probe.bind((LOOPBACK_HOST, 0))
        return [probe.getsockname()[1] for probe in sockets]
    finally:
        for probe in sockets:
            probe.close()


def _section(config: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    current: Any = config
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, dict) else None


def _require_loopback(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _require_loopback(item)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _require_loopback(item)
        return
    if isinstance(value, str) and ALL_INTERFACES in value:
        raise RuntimeError(
            f"Resident setting binds every network interface ({value!r}); "
            "host-process residents listen on loopback only"
        )


def _failure(resident: _HostResident) -> str:
    for item in resident.processes:
        if item.process.returncode is not None:
            return f"{item.name} exited with code {item.process.returncode}"
        if item.output.done() and not item.output.cancelled() and item.output.exception():
            return f"recording {item.name} output failed: {item.output.exception()}"
    return ""


def _signal_group(pgid: int, sig: signal.Signals) -> None:
    with suppress(ProcessLookupError):
        os.killpg(pgid, sig)


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _command_line(pid: int, timeout: float) -> str:
    result = subprocess.run(
        ["ps", "-ww", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


async def _record_output(stream: asyncio.StreamReader, log_path: Path, source: str) -> None:
    """Append process output to its log as ``<timestamp> [<source>] <line>``."""
    pending = b""
    with log_path.open("ab") as log:
        while chunk := await stream.read(LOG_CHUNK_BYTES):
            *lines, pending = (pending + chunk).split(b"\n")
            if len(pending) >= LOG_CHUNK_BYTES:
                lines.append(pending)
                pending = b""
            _write_log_lines(log, source, lines)
        if pending:
            _write_log_lines(log, source, [pending])


def _write_log_lines(log: BinaryIO, source: str, lines: list[bytes]) -> None:
    if not lines:
        return
    prefix = f"{datetime.now(UTC).isoformat()} [{source}] ".encode()
    log.write(b"".join(prefix + line.rstrip(b"\r") + b"\n" for line in lines))
    log.flush()


def _tail_logs(paths: list[Path], lines: int) -> str:
    return "\n".join(text for path in paths if (text := _tail_text(path, lines)))


def _tail_text(path: Path, lines: int) -> str:
    """Return the last ``lines`` lines of ``path`` without reading the whole file."""
    if lines <= 0:
        return ""
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        return ""
    with handle:
        position = handle.seek(0, os.SEEK_END)
        data = b""
        while position > 0 and data.count(b"\n") <= lines:
            step = min(LOG_CHUNK_BYTES, position)
            position -= step
            handle.seek(position)
            data = handle.read(step) + data
    return b"\n".join(data.splitlines()[-lines:]).decode("utf-8", errors="replace")
