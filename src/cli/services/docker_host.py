"""Docker-mode preflight — validate a single Docker host before ``niuu up``.

Docker mode runs the whole platform as containers on one machine (the
reference target is an NVIDIA DGX Spark, but any Docker host works). The
checks here replace the mini-mode host checks: instead of a ``claude``
binary and embedded PostgreSQL binaries we need a reachable Docker daemon,
Compose v2, the NVIDIA container runtime when a GPU is expected, a data
directory with room for models, and free ports.

Every check returns a :class:`PreflightResult` so the existing
``format_results`` / ``has_failures`` helpers apply unchanged. Failures carry
the remedy in the message.
"""

from __future__ import annotations

import json
import os
import platform as _platform
import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cli.services.preflight import PreflightResult, check_git, check_port_available

DOCKER_PERMISSION_REMEDY = (
    "Your user cannot reach the Docker socket. Run "
    "`sudo usermod -aG docker $USER` and log in again (or `newgrp docker`)."
)
NVIDIA_RUNTIME_REMEDY = (
    "Install the NVIDIA Container Toolkit and register the runtime: "
    "`sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`."
)


@dataclass
class DockerPreflightConfig:
    """Configuration for Docker-mode preflight checks."""

    data_dir: str = "/var/lib/niuu"
    ports: list[int] = field(default_factory=lambda: [8080])
    min_disk_space_bytes: int = 50 * 1024**3
    require_gpu: bool = False
    registry_hosts: list[str] = field(default_factory=lambda: ["ghcr.io"])
    # Hosts the platform needs for image pulls, model downloads and providers.
    outbound_hosts: list[str] = field(
        default_factory=lambda: ["ghcr.io", "huggingface.co", "api.anthropic.com", "api.openai.com"]
    )
    registry_port: int = 443
    registry_connect_timeout_seconds: float = 3.0
    command_timeout_seconds: float = 15.0


@dataclass(frozen=True)
class GpuFacts:
    """One GPU as reported by ``nvidia-smi``."""

    name: str
    memory_total_mib: int
    driver_version: str


@dataclass(frozen=True)
class HostFacts:
    """Facts about the Docker host, shown on the wizard's welcome step."""

    hostname: str
    os_name: str
    os_version: str
    arch: str
    cpu_count: int
    memory_total_bytes: int
    docker_version: str
    compose_version: str
    nvidia_runtime: bool
    gpus: list[GpuFacts]
    data_dir: str
    disk_free_bytes: int
    disk_total_bytes: int
    # How `niuu up` published the platform: where it listens and the address
    # the wizard's runtime step shows. Empty when not started through the CLI.
    bind_host: str = ""
    external_host: str = ""
    port: int = 0
    skuld_image: str = ""
    # Preflight results as `niuu up` saw them (name, passed, warn_only, message),
    # so the wizard can show host-side checks the platform cannot run itself.
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def check_docker_binary() -> PreflightResult:
    """Verify the ``docker`` CLI is on PATH."""
    resolved = shutil.which("docker")
    if not resolved:
        return PreflightResult(
            name="docker",
            passed=False,
            message="docker not found in PATH. Install Docker Engine: "
            "https://docs.docker.com/engine/install/",
        )
    return PreflightResult(name="docker", passed=True, message=f"docker found: {resolved}")


def docker_info(config: DockerPreflightConfig) -> dict[str, object] | PreflightResult:
    """Return ``docker info`` as a dict, or a failed result explaining why not."""
    docker = shutil.which("docker")
    if not docker:
        return check_docker_binary()
    try:
        result = _run([docker, "info", "--format", "{{json .}}"], config.command_timeout_seconds)
    except subprocess.TimeoutExpired:
        return PreflightResult(
            name="docker daemon",
            passed=False,
            message=f"`docker info` timed out after {config.command_timeout_seconds:g}s. "
            "Is the daemon running? Try `sudo systemctl status docker`.",
        )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "permission denied" in stderr.lower():
            return PreflightResult(
                name="docker daemon", passed=False, message=DOCKER_PERMISSION_REMEDY
            )
        return PreflightResult(
            name="docker daemon",
            passed=False,
            message=f"Docker daemon not reachable: {stderr or 'unknown error'}. "
            "Start it with `sudo systemctl start docker`.",
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return PreflightResult(
            name="docker daemon",
            passed=False,
            message="`docker info` returned unparsable output; upgrade Docker Engine to 24+.",
        )
    if not isinstance(payload, dict):
        return PreflightResult(
            name="docker daemon",
            passed=False,
            message="`docker info` returned an unexpected shape; upgrade Docker Engine to 24+.",
        )
    return payload


def docker_socket_gid(
    config: DockerPreflightConfig,
    socket_path: str = "/var/run/docker.sock",
) -> int | None:
    """Group id of the Docker socket as containers will see it.

    On Linux the socket bind-mounted into a container keeps the host's group
    (usually ``docker``), so the host stat is right. Docker Desktop runs the
    daemon in a VM and presents the socket as ``root:root`` inside containers,
    whatever the host symlink says, so the root group is what grants access.
    Returns ``None`` when no socket exists.
    """
    info = docker_info(config)
    if not isinstance(info, PreflightResult):
        operating_system = str(info.get("OperatingSystem", ""))
        if "Docker Desktop" in operating_system:
            return 0
    try:
        return os.stat(socket_path).st_gid
    except OSError:
        return None


def check_docker_daemon(config: DockerPreflightConfig) -> PreflightResult:
    """Verify the Docker daemon is reachable by the current user."""
    info = docker_info(config)
    if isinstance(info, PreflightResult):
        return info
    version = str(info.get("ServerVersion", "unknown"))
    arch = str(info.get("Architecture", "unknown"))
    return PreflightResult(
        name="docker daemon",
        passed=True,
        message=f"Docker Engine {version} ({arch}) reachable.",
    )


def check_docker_compose(config: DockerPreflightConfig) -> PreflightResult:
    """Verify Docker Compose v2 is installed as a docker plugin."""
    docker = shutil.which("docker")
    if not docker:
        return check_docker_binary()
    try:
        result = _run([docker, "compose", "version", "--short"], config.command_timeout_seconds)
    except subprocess.TimeoutExpired:
        return PreflightResult(
            name="docker compose",
            passed=False,
            message="`docker compose version` timed out.",
        )
    if result.returncode != 0:
        return PreflightResult(
            name="docker compose",
            passed=False,
            message="Docker Compose v2 plugin not found. Install `docker-compose-plugin`: "
            "https://docs.docker.com/compose/install/linux/",
        )
    return PreflightResult(
        name="docker compose",
        passed=True,
        message=f"Docker Compose {result.stdout.strip()} found.",
    )


def _nvidia_runtime_registered(info: dict[str, object]) -> bool:
    runtimes = info.get("Runtimes")
    return isinstance(runtimes, dict) and "nvidia" in runtimes


def check_nvidia_runtime(config: DockerPreflightConfig) -> PreflightResult:
    """Verify the NVIDIA container runtime is registered with Docker."""
    info = docker_info(config)
    if isinstance(info, PreflightResult):
        return PreflightResult(
            name="nvidia runtime",
            passed=not config.require_gpu,
            warn_only=not config.require_gpu,
            message="Cannot inspect Docker runtimes because the daemon is unreachable.",
        )
    if _nvidia_runtime_registered(info):
        return PreflightResult(
            name="nvidia runtime",
            passed=True,
            message="NVIDIA container runtime registered with Docker.",
        )
    if config.require_gpu:
        return PreflightResult(name="nvidia runtime", passed=False, message=NVIDIA_RUNTIME_REMEDY)
    return PreflightResult(
        name="nvidia runtime",
        passed=True,
        warn_only=True,
        message="No NVIDIA container runtime; local models and GPU sandboxes are unavailable.",
    )


def query_gpus(config: DockerPreflightConfig) -> list[GpuFacts]:
    """Return the GPUs visible to ``nvidia-smi``; empty when it is absent or fails."""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return []
    try:
        result = _run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            config.command_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return []
    if result.returncode != 0:
        return []
    gpus: list[GpuFacts] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            memory = int(float(parts[1]))
        except ValueError:
            continue
        gpus.append(GpuFacts(name=parts[0], memory_total_mib=memory, driver_version=parts[2]))
    return gpus


def check_gpu(config: DockerPreflightConfig) -> PreflightResult:
    """Verify at least one NVIDIA GPU is visible on the host."""
    gpus = query_gpus(config)
    if gpus:
        summary = ", ".join(f"{gpu.name} ({gpu.memory_total_mib // 1024} GiB)" for gpu in gpus)
        return PreflightResult(name="gpu", passed=True, message=f"GPU: {summary}")
    if config.require_gpu:
        return PreflightResult(
            name="gpu",
            passed=False,
            message="No NVIDIA GPU visible (`nvidia-smi` missing or failing). Install the "
            "driver, or set `docker.require_gpu: false` to run without local models.",
        )
    return PreflightResult(
        name="gpu",
        passed=True,
        warn_only=True,
        message="No NVIDIA GPU visible; cloud models only.",
    )


def check_data_dir(config: DockerPreflightConfig) -> PreflightResult:
    """Verify the data directory exists and is writable, creating it if possible."""
    data_dir = Path(config.data_dir).expanduser()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return PreflightResult(
            name="data dir",
            passed=False,
            message=f"Cannot create data directory '{data_dir}': {exc}. "
            f"Create it with `sudo mkdir -p {data_dir} && sudo chown $USER {data_dir}` "
            "or set `docker.data_dir` to a writable path.",
        )
    if not os.access(data_dir, os.W_OK):
        return PreflightResult(
            name="data dir",
            passed=False,
            message=f"Data directory '{data_dir}' is not writable. "
            f"Run `sudo chown $USER {data_dir}` or set `docker.data_dir`.",
        )
    return PreflightResult(
        name="data dir", passed=True, message=f"Data directory ready: {data_dir}"
    )


def check_disk_space(config: DockerPreflightConfig) -> PreflightResult:
    """Verify the data directory has room for images, databases and models."""
    data_dir = Path(config.data_dir).expanduser()
    check_path = data_dir if data_dir.exists() else data_dir.parent
    if not check_path.exists():
        check_path = Path.home()
    try:
        usage = shutil.disk_usage(check_path)
    except OSError as exc:
        return PreflightResult(
            name="disk space",
            passed=True,
            warn_only=True,
            message=f"Could not check disk space: {exc}",
        )
    free_gib = usage.free / 1024**3
    if usage.free < config.min_disk_space_bytes:
        need_gib = config.min_disk_space_bytes / 1024**3
        return PreflightResult(
            name="disk space",
            passed=True,
            warn_only=True,
            message=f"{free_gib:.0f} GiB free on {check_path}; local models need about "
            f"{need_gib:.0f} GiB. Free space or set `docker.data_dir` to a larger disk.",
        )
    return PreflightResult(
        name="disk space",
        passed=True,
        message=f"{free_gib:.0f} GiB free on {check_path}.",
    )


def check_ports(config: DockerPreflightConfig) -> list[PreflightResult]:
    """Check every published port is free on the host."""
    return [check_port_available(port) for port in config.ports]


def check_registry_reachable(config: DockerPreflightConfig) -> list[PreflightResult]:
    """Warn when an image registry cannot be reached (pulls will fail)."""
    results: list[PreflightResult] = []
    for host in config.registry_hosts:
        try:
            with socket.create_connection(
                (host, config.registry_port),
                timeout=config.registry_connect_timeout_seconds,
            ):
                pass
        except OSError as exc:
            results.append(
                PreflightResult(
                    name=f"registry {host}",
                    passed=True,
                    warn_only=True,
                    message=f"Cannot reach {host}:{config.registry_port} ({exc}). "
                    "Image pulls will fail until the network is available.",
                )
            )
            continue
        results.append(
            PreflightResult(
                name=f"registry {host}",
                passed=True,
                message=f"{host} reachable.",
            )
        )
    return results


def check_outbound_network(config: DockerPreflightConfig) -> PreflightResult:
    """One row: which of the hosts the platform depends on are reachable."""
    reachable: list[str] = []
    unreachable: list[str] = []
    for host in config.outbound_hosts:
        try:
            with socket.create_connection(
                (host, config.registry_port),
                timeout=config.registry_connect_timeout_seconds,
            ):
                pass
        except OSError:
            unreachable.append(host)
            continue
        reachable.append(host)
    if unreachable:
        return PreflightResult(
            name="outbound network",
            passed=True,
            warn_only=True,
            message=(
                f"Cannot reach {', '.join(unreachable)}. Image pulls, model downloads or "
                "provider calls will fail until the network is available."
                + (f" Reachable: {', '.join(reachable)}." if reachable else "")
            ),
        )
    return PreflightResult(
        name="outbound network",
        passed=True,
        message=f"{', '.join(reachable)} reachable.",
    )


def run_docker_preflight_checks(config: DockerPreflightConfig) -> list[PreflightResult]:
    """Run every Docker-mode check and return all results."""
    results: list[PreflightResult] = [
        check_docker_binary(),
        check_docker_daemon(config),
        check_docker_compose(config),
        check_nvidia_runtime(config),
        check_gpu(config),
        check_data_dir(config),
        check_disk_space(config),
    ]
    results.extend(check_ports(config))
    results.append(check_outbound_network(config))
    results.append(check_git())
    return results


def preflight_results_as_facts(results: list[PreflightResult]) -> list[dict[str, Any]]:
    return [
        {
            "name": r.name,
            "passed": r.passed,
            "warn_only": r.warn_only,
            "message": r.message,
        }
        for r in results
    ]


def _memory_total_bytes() -> int:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return 0
    return int(pages) * int(page_size)


def _os_release() -> tuple[str, str]:
    """Return (name, version) from /etc/os-release, falling back to platform."""
    os_release = Path("/etc/os-release")
    if os_release.exists():
        values: dict[str, str] = {}
        for line in os_release.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep:
                values[key.strip()] = value.strip().strip('"')
        name = values.get("NAME") or _platform.system()
        version = values.get("VERSION_ID") or _platform.release()
        return name, version
    return _platform.system(), _platform.mac_ver()[0] or _platform.release()


def collect_host_facts(
    config: DockerPreflightConfig,
    *,
    bind_host: str = "",
    external_host: str = "",
    port: int = 0,
    skuld_image: str = "",
    checks: list[dict[str, Any]] | None = None,
) -> HostFacts:
    """Gather host facts for the wizard; never raises for missing tools."""
    info = docker_info(config)
    docker_version = ""
    nvidia_runtime = False
    if not isinstance(info, PreflightResult):
        docker_version = str(info.get("ServerVersion", ""))
        nvidia_runtime = _nvidia_runtime_registered(info)

    compose_version = ""
    compose = check_docker_compose(config)
    if compose.passed and not compose.warn_only:
        compose_version = compose.message.removeprefix("Docker Compose ").removesuffix(" found.")

    data_dir = Path(config.data_dir).expanduser()
    disk_path = data_dir if data_dir.exists() else Path.home()
    try:
        usage = shutil.disk_usage(disk_path)
        disk_free, disk_total = usage.free, usage.total
    except OSError:
        disk_free, disk_total = 0, 0

    os_name, os_version = _os_release()
    return HostFacts(
        hostname=socket.gethostname(),
        os_name=os_name,
        os_version=os_version,
        arch=_platform.machine(),
        cpu_count=os.cpu_count() or 0,
        memory_total_bytes=_memory_total_bytes(),
        docker_version=docker_version,
        compose_version=compose_version,
        nvidia_runtime=nvidia_runtime,
        gpus=query_gpus(config),
        data_dir=str(data_dir),
        disk_free_bytes=disk_free,
        disk_total_bytes=disk_total,
        bind_host=bind_host,
        external_host=external_host,
        port=port,
        skuld_image=skuld_image,
        checks=list(checks or []),
    )
