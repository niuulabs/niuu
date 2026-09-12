"""Tests for Docker-mode preflight checks and host facts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from cli.services.docker_host import (
    DOCKER_PERMISSION_REMEDY,
    NVIDIA_RUNTIME_REMEDY,
    DockerPreflightConfig,
    GpuFacts,
    check_data_dir,
    check_disk_space,
    check_docker_binary,
    check_docker_compose,
    check_docker_daemon,
    check_gpu,
    check_nvidia_runtime,
    check_ports,
    check_registry_reachable,
    collect_host_facts,
    docker_info,
    docker_socket_gid,
    query_gpus,
    run_docker_preflight_checks,
)
from cli.services.preflight import PreflightResult, has_failures

MOD = "cli.services.docker_host"


def _proc(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def config(tmp_path: Path) -> DockerPreflightConfig:
    return DockerPreflightConfig(data_dir=str(tmp_path / "niuu"), ports=[18080], require_gpu=True)


class TestDockerBinary:
    def test_missing(self) -> None:
        with patch(f"{MOD}.shutil.which", return_value=None):
            result = check_docker_binary()
        assert result.passed is False
        assert "Install Docker Engine" in result.message

    def test_found(self) -> None:
        with patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"):
            result = check_docker_binary()
        assert result.passed is True


class TestDockerDaemon:
    def test_permission_denied_has_remedy(self, config: DockerPreflightConfig) -> None:
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"),
            patch(
                f"{MOD}.subprocess.run",
                return_value=_proc(1, stderr="permission denied while trying to connect"),
            ),
        ):
            result = check_docker_daemon(config)
        assert result.passed is False
        assert result.message == DOCKER_PERMISSION_REMEDY

    def test_daemon_down(self, config: DockerPreflightConfig) -> None:
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"),
            patch(
                f"{MOD}.subprocess.run",
                return_value=_proc(1, stderr="Cannot connect to the Docker daemon"),
            ),
        ):
            result = check_docker_daemon(config)
        assert result.passed is False
        assert "systemctl start docker" in result.message

    def test_timeout(self, config: DockerPreflightConfig) -> None:
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"),
            patch(f"{MOD}.subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 15)),
        ):
            result = check_docker_daemon(config)
        assert result.passed is False
        assert "timed out" in result.message

    def test_unparsable(self, config: DockerPreflightConfig) -> None:
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"),
            patch(f"{MOD}.subprocess.run", return_value=_proc(0, stdout="not json")),
        ):
            result = check_docker_daemon(config)
        assert result.passed is False

    def test_unexpected_shape(self, config: DockerPreflightConfig) -> None:
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"),
            patch(f"{MOD}.subprocess.run", return_value=_proc(0, stdout="[1, 2]")),
        ):
            result = check_docker_daemon(config)
        assert result.passed is False

    def test_missing_binary_short_circuits(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.shutil.which", return_value=None):
            result = docker_info(config)
        assert isinstance(result, PreflightResult)
        assert result.name == "docker"

    def test_reachable(self, config: DockerPreflightConfig) -> None:
        info = {"ServerVersion": "27.3.1", "Architecture": "aarch64", "Runtimes": {"runc": {}}}
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"),
            patch(f"{MOD}.subprocess.run", return_value=_proc(0, stdout=json.dumps(info))),
        ):
            result = check_docker_daemon(config)
        assert result.passed is True
        assert "27.3.1" in result.message


class TestDockerCompose:
    def test_missing_binary(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.shutil.which", return_value=None):
            assert check_docker_compose(config).passed is False

    def test_plugin_missing(self, config: DockerPreflightConfig) -> None:
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"),
            patch(f"{MOD}.subprocess.run", return_value=_proc(1, stderr="not a docker command")),
        ):
            result = check_docker_compose(config)
        assert result.passed is False
        assert "docker-compose-plugin" in result.message

    def test_timeout(self, config: DockerPreflightConfig) -> None:
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"),
            patch(f"{MOD}.subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 15)),
        ):
            assert check_docker_compose(config).passed is False

    def test_found(self, config: DockerPreflightConfig) -> None:
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"),
            patch(f"{MOD}.subprocess.run", return_value=_proc(0, stdout="2.40.3\n")),
        ):
            result = check_docker_compose(config)
        assert result.passed is True
        assert "2.40.3" in result.message


class TestNvidiaRuntime:
    def test_registered(self, config: DockerPreflightConfig) -> None:
        info = {"Runtimes": {"nvidia": {"path": "nvidia-container-runtime"}}}
        with patch(f"{MOD}.docker_info", return_value=info):
            assert check_nvidia_runtime(config).passed is True

    def test_missing_required(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.docker_info", return_value={"Runtimes": {"runc": {}}}):
            result = check_nvidia_runtime(config)
        assert result.passed is False
        assert result.message == NVIDIA_RUNTIME_REMEDY

    def test_missing_optional_warns(self, config: DockerPreflightConfig) -> None:
        optional = replace(config, require_gpu=False)
        with patch(f"{MOD}.docker_info", return_value={"Runtimes": {"runc": {}}}):
            result = check_nvidia_runtime(optional)
        assert result.passed is True
        assert result.warn_only is True

    def test_daemon_unreachable_required(self, config: DockerPreflightConfig) -> None:
        failed = PreflightResult(name="docker daemon", passed=False, message="down")
        with patch(f"{MOD}.docker_info", return_value=failed):
            result = check_nvidia_runtime(config)
        assert result.passed is False

    def test_daemon_unreachable_optional(self, config: DockerPreflightConfig) -> None:
        failed = PreflightResult(name="docker daemon", passed=False, message="down")
        optional = replace(config, require_gpu=False)
        with patch(f"{MOD}.docker_info", return_value=failed):
            result = check_nvidia_runtime(optional)
        assert result.passed is True
        assert result.warn_only is True


class TestGpu:
    def test_query_parses_rows(self, config: DockerPreflightConfig) -> None:
        stdout = "NVIDIA GB10, 131072, 570.86.10\nbad row\nX, notanumber, 1\n"
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch(f"{MOD}.subprocess.run", return_value=_proc(0, stdout=stdout)),
        ):
            gpus = query_gpus(config)
        assert gpus == [
            GpuFacts(name="NVIDIA GB10", memory_total_mib=131072, driver_version="570.86.10")
        ]

    def test_query_missing_binary(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.shutil.which", return_value=None):
            assert query_gpus(config) == []

    def test_query_failure(self, config: DockerPreflightConfig) -> None:
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch(f"{MOD}.subprocess.run", return_value=_proc(9, stderr="no devices")),
        ):
            assert query_gpus(config) == []

    def test_query_timeout(self, config: DockerPreflightConfig) -> None:
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch(f"{MOD}.subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 15)),
        ):
            assert query_gpus(config) == []

    def test_check_found(self, config: DockerPreflightConfig) -> None:
        gpu = GpuFacts(name="NVIDIA GB10", memory_total_mib=131072, driver_version="570")
        with patch(f"{MOD}.query_gpus", return_value=[gpu]):
            result = check_gpu(config)
        assert result.passed is True
        assert "128 GiB" in result.message

    def test_check_missing_required(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.query_gpus", return_value=[]):
            result = check_gpu(config)
        assert result.passed is False
        assert "require_gpu" in result.message

    def test_check_missing_optional(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.query_gpus", return_value=[]):
            result = check_gpu(replace(config, require_gpu=False))
        assert result.passed is True
        assert result.warn_only is True


class TestDockerSocketGid:
    def test_docker_desktop_uses_root_group(
        self, config: DockerPreflightConfig, tmp_path: Path
    ) -> None:
        sock = tmp_path / "docker.sock"
        sock.write_text("")
        with patch(f"{MOD}.docker_info", return_value={"OperatingSystem": "Docker Desktop"}):
            assert docker_socket_gid(config, str(sock)) == 0

    def test_linux_uses_host_socket_group(
        self, config: DockerPreflightConfig, tmp_path: Path
    ) -> None:
        sock = tmp_path / "docker.sock"
        sock.write_text("")
        with patch(f"{MOD}.docker_info", return_value={"OperatingSystem": "Ubuntu 24.04"}):
            assert docker_socket_gid(config, str(sock)) == sock.stat().st_gid

    def test_missing_socket(self, config: DockerPreflightConfig, tmp_path: Path) -> None:
        failed = PreflightResult(name="docker", passed=False, message="missing")
        with patch(f"{MOD}.docker_info", return_value=failed):
            assert docker_socket_gid(config, str(tmp_path / "nope.sock")) is None


class TestDataDir:
    def test_creates_and_passes(self, config: DockerPreflightConfig) -> None:
        result = check_data_dir(config)
        assert result.passed is True
        assert Path(config.data_dir).is_dir()

    def test_cannot_create(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.Path.mkdir", side_effect=OSError("read-only")):
            result = check_data_dir(config)
        assert result.passed is False
        assert "sudo mkdir" in result.message

    def test_not_writable(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.os.access", return_value=False):
            result = check_data_dir(config)
        assert result.passed is False
        assert "chown" in result.message


class TestDiskSpace:
    def test_enough(self, config: DockerPreflightConfig) -> None:
        usage = type("U", (), {"free": 200 * 1024**3, "total": 400 * 1024**3})()
        with patch(f"{MOD}.shutil.disk_usage", return_value=usage):
            result = check_disk_space(config)
        assert result.passed is True
        assert result.warn_only is False

    def test_low_warns(self, config: DockerPreflightConfig) -> None:
        usage = type("U", (), {"free": 5 * 1024**3, "total": 400 * 1024**3})()
        with patch(f"{MOD}.shutil.disk_usage", return_value=usage):
            result = check_disk_space(config)
        assert result.passed is True
        assert result.warn_only is True
        assert "50 GiB" in result.message

    def test_missing_dir_falls_back_to_home(self, tmp_path: Path) -> None:
        cfg = DockerPreflightConfig(data_dir=str(tmp_path / "missing" / "deeper"))
        usage = type("U", (), {"free": 200 * 1024**3, "total": 400 * 1024**3})()
        with patch(f"{MOD}.shutil.disk_usage", return_value=usage) as disk_usage:
            check_disk_space(cfg)
        assert disk_usage.call_args.args[0] == Path.home()

    def test_oserror_warns(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.shutil.disk_usage", side_effect=OSError("boom")):
            result = check_disk_space(config)
        assert result.warn_only is True


class TestPortsAndRegistry:
    def test_ports_delegate(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.check_port_available") as check:
            check.return_value = PreflightResult(name="port 18080", passed=True, message="ok")
            results = check_ports(config)
        assert [r.name for r in results] == ["port 18080"]

    def test_registry_reachable(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.socket.create_connection") as connect:
            connect.return_value.__enter__.return_value = None
            results = check_registry_reachable(config)
        assert results[0].passed is True
        assert results[0].warn_only is False

    def test_registry_unreachable_warns(self, config: DockerPreflightConfig) -> None:
        with patch(f"{MOD}.socket.create_connection", side_effect=OSError("timed out")):
            results = check_registry_reachable(config)
        assert results[0].passed is True
        assert results[0].warn_only is True
        assert "ghcr.io" in results[0].message


class TestRunAll:
    def test_collects_every_check(self, config: DockerPreflightConfig) -> None:
        ok = PreflightResult(name="x", passed=True, message="ok")
        with (
            patch(f"{MOD}.check_docker_binary", return_value=ok),
            patch(f"{MOD}.check_docker_daemon", return_value=ok),
            patch(f"{MOD}.check_docker_compose", return_value=ok),
            patch(f"{MOD}.check_nvidia_runtime", return_value=ok),
            patch(f"{MOD}.check_gpu", return_value=ok),
            patch(f"{MOD}.check_data_dir", return_value=ok),
            patch(f"{MOD}.check_disk_space", return_value=ok),
            patch(f"{MOD}.check_ports", return_value=[ok]),
            patch(f"{MOD}.check_registry_reachable", return_value=[ok]),
            patch(f"{MOD}.check_git", return_value=ok),
        ):
            results = run_docker_preflight_checks(config)
        assert len(results) == 10
        assert has_failures(results) is False


class TestHostFacts:
    def test_collects_from_docker_and_gpu(
        self, config: DockerPreflightConfig, tmp_path: Path
    ) -> None:
        info = {"ServerVersion": "27.3.1", "Runtimes": {"nvidia": {}}}
        gpu = GpuFacts(name="NVIDIA GB10", memory_total_mib=131072, driver_version="570")
        compose = PreflightResult(
            name="docker compose", passed=True, message="Docker Compose 2.40.3 found."
        )
        usage = type("U", (), {"free": 10, "total": 20})()
        os_release = tmp_path / "os-release"
        os_release.write_text('NAME="Ubuntu"\nVERSION_ID="24.04"\n')
        with (
            patch(f"{MOD}.docker_info", return_value=info),
            patch(f"{MOD}.check_docker_compose", return_value=compose),
            patch(f"{MOD}.query_gpus", return_value=[gpu]),
            patch(f"{MOD}.shutil.disk_usage", return_value=usage),
            patch(f"{MOD}.Path.exists", return_value=True),
            patch(f"{MOD}.Path.read_text", return_value=os_release.read_text()),
        ):
            facts = collect_host_facts(config)
        assert facts.docker_version == "27.3.1"
        assert facts.compose_version == "2.40.3"
        assert facts.nvidia_runtime is True
        assert facts.gpus == [gpu]
        assert facts.os_name == "Ubuntu"
        assert facts.os_version == "24.04"
        assert facts.disk_free_bytes == 10
        payload = json.loads(facts.to_json())
        assert payload["gpus"][0]["name"] == "NVIDIA GB10"

    def test_tolerates_missing_everything(self, config: DockerPreflightConfig) -> None:
        failed = PreflightResult(name="docker", passed=False, message="missing")
        with (
            patch(f"{MOD}.docker_info", return_value=failed),
            patch(f"{MOD}.check_docker_compose", return_value=failed),
            patch(f"{MOD}.query_gpus", return_value=[]),
            patch(f"{MOD}.shutil.disk_usage", side_effect=OSError("nope")),
            patch(f"{MOD}.Path.exists", return_value=False),
        ):
            facts = collect_host_facts(config)
        assert facts.docker_version == ""
        assert facts.compose_version == ""
        assert facts.nvidia_runtime is False
        assert facts.gpus == []
        assert facts.disk_total_bytes == 0
        assert facts.os_name

    def test_memory_total_handles_missing_sysconf(self, config: DockerPreflightConfig) -> None:
        failed = PreflightResult(name="docker", passed=False, message="missing")
        with (
            patch(f"{MOD}.docker_info", return_value=failed),
            patch(f"{MOD}.check_docker_compose", return_value=failed),
            patch(f"{MOD}.query_gpus", return_value=[]),
            patch(f"{MOD}.os.sysconf", side_effect=ValueError("no such name")),
        ):
            facts = collect_host_facts(config)
        assert facts.memory_total_bytes == 0
