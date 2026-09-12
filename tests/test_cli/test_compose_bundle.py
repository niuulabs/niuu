"""Tests for the docker-mode compose bundle renderer."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from cli.config import CLISettings
from cli.services import compose_bundle as sc
from cli.services.docker_host import GpuFacts, HostFacts

MOD = "cli.services.compose_bundle"


@pytest.fixture
def settings(tmp_path: Path) -> CLISettings:
    return CLISettings(
        mode="docker",
        docker={
            "data_dir": str(tmp_path / "data"),
            "compose_dir": str(tmp_path / "bundle"),
            "image": "ghcr.io/niuulabs/niuu:test",
        },
    )


@pytest.fixture
def facts(tmp_path: Path) -> HostFacts:
    return HostFacts(
        hostname="docker",
        os_name="Ubuntu",
        os_version="24.04",
        arch="aarch64",
        cpu_count=20,
        memory_total_bytes=128 * 1024**3,
        docker_version="27.3.1",
        compose_version="2.40.3",
        nvidia_runtime=True,
        gpus=[GpuFacts(name="NVIDIA GB10", memory_total_mib=131072, driver_version="570")],
        data_dir=str(tmp_path / "data"),
        disk_free_bytes=3 * 1024**4,
        disk_total_bytes=4 * 1024**4,
    )


class TestSecrets:
    def test_generated_once_and_private(self, settings: CLISettings) -> None:
        first = sc.load_or_create_secrets(settings)
        second = sc.load_or_create_secrets(settings)
        assert first == second
        assert len(first.postgres_password) >= 24
        assert len(first.credential_key) == 44
        mode = stat.S_IMODE(sc.bundle_paths(settings).secrets_file.stat().st_mode)
        assert mode == stat.S_IRUSR | stat.S_IWUSR

    def test_configured_password_wins(self, settings: CLISettings) -> None:
        sc.load_or_create_secrets(settings)
        settings.docker.postgres_password = "operator-set"
        result = sc.load_or_create_secrets(settings)
        assert result.postgres_password == "operator-set"
        text = sc.bundle_paths(settings).secrets_file.read_text()
        assert "NIUU_POSTGRES_PASSWORD=operator-set" in text

    def test_parse_env_file_ignores_comments(self) -> None:
        parsed = sc._parse_env_file("# c\nA=1\n\nB = two\nnoequals\n")
        assert parsed == {"A": "1", "B": "two"}


class TestRender:
    def test_compose_document_shape(self, settings: CLISettings) -> None:
        doc = sc.render_compose(settings)
        services = doc["services"]
        assert set(services) == {"postgres", "niuu"}
        niuu = services["niuu"]
        assert niuu["entrypoint"] == ["/opt/venv/bin/niuu"]
        assert niuu["command"][:2] == ["platform", "up"]
        assert "/var/run/docker.sock:/var/run/docker.sock" in niuu["volumes"]
        data = str(sc.data_dir(settings))
        assert f"{data}:{data}" in niuu["volumes"]
        assert niuu["ports"] == ["${NIUU_BIND_HOST}:8080:8080"]
        assert niuu["depends_on"] == {"postgres": {"condition": "service_healthy"}}
        env = niuu["environment"]
        assert env["NIUU_MODE"] == "mini"
        assert env["NIUU_DATABASE_MODE"] == "external"
        assert env["DATABASE__HOST"] == "postgres"
        assert env["NIUU_POD_MANAGER__ADAPTER"] == sc.DOCKER_POD_MANAGER_ADAPTER
        assert env["NIUU_POD_MANAGER__NETWORK"] == "${COMPOSE_PROJECT_NAME}_default"
        assert env["NIUU_POD_MANAGER__PLATFORM_URL"] == "http://niuu:8080"
        store = json.loads(env["CREDENTIAL_STORE"])
        assert store["secret_kwargs_env"] == {"encryption_key": "NIUU_CREDENTIAL_KEY"}
        assert store["kwargs"]["base_dir"] == f"{data}/credentials"
        assert "NIUU_BIFROST" not in env
        assert services["postgres"]["environment"]["POSTGRES_DB"] == "volundr"

    def test_vllm_service_when_enabled(self, settings: CLISettings) -> None:
        settings.docker.vllm.enabled = True
        settings.docker.vllm.model = "nvidia/Nemotron-3-Nano-30B-A3B"
        doc = sc.render_compose(settings)
        vllm = doc["services"]["vllm"]
        assert "--model" in vllm["command"]
        assert vllm["command"][vllm["command"].index("--model") + 1] == settings.docker.vllm.model
        devices = vllm["deploy"]["resources"]["reservations"]["devices"]
        assert devices[0]["driver"] == "nvidia"
        assert doc["services"]["niuu"]["depends_on"]["vllm"] == {"condition": "service_started"}
        bifrost = json.loads(doc["services"]["niuu"]["environment"]["NIUU_BIFROST"])
        assert bifrost["providers"]["vllm"]["base_url"] == "http://vllm:8000/v1"
        assert bifrost["providers"]["vllm"]["models"] == [settings.docker.vllm.model]

    def test_env_file_values(self, settings: CLISettings) -> None:
        text = sc.render_env(settings, external_host="10.0.0.5", docker_gid=999)
        parsed = sc._parse_env_file(text)
        assert parsed["NIUU_IMAGE"] == "ghcr.io/niuulabs/niuu:test"
        assert parsed["NIUU_EXTERNAL_HOST"] == "10.0.0.5"
        assert parsed["NIUU_DOCKER_GID"] == "999"
        assert parsed["NIUU_BIND_HOST"] == "0.0.0.0"
        assert parsed["COMPOSE_PROJECT_NAME"] == "niuu"

    def test_env_file_without_docker_socket_falls_back_to_gid(self, settings: CLISettings) -> None:
        with patch(f"{MOD}.os.getgid", return_value=20):
            parsed = sc._parse_env_file(sc.render_env(settings, external_host="h", docker_gid=None))
        assert parsed["NIUU_DOCKER_GID"] == "20"


class TestBundle:
    def test_write_bundle_creates_everything(self, settings: CLISettings, facts: HostFacts) -> None:
        with patch(f"{MOD}.docker_socket_gid", return_value=999):
            paths = sc.write_bundle(settings, host_facts=facts, external_host="10.0.0.5")
        assert paths.compose_file.exists()
        doc = yaml.safe_load(paths.compose_file.read_text())
        assert doc["name"] == "${COMPOSE_PROJECT_NAME}"
        assert "niuu" in doc["services"]
        assert paths.secrets_file.exists()
        assert paths.env_file.exists()
        stored = json.loads(paths.host_facts_file.read_text())
        assert stored["gpus"][0]["name"] == "NVIDIA GB10"
        assert (sc.data_dir(settings) / sc.HOST_FACTS_FILE).exists()
        for sub in sc.data_subdirs(sc.data_dir(settings)).values():
            assert sub.is_dir()

    def test_compose_command_binds_bundle(self, settings: CLISettings) -> None:
        with patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"):
            cmd = sc.compose_command(settings, "ps")
        paths = sc.bundle_paths(settings)
        assert cmd[:2] == ["/usr/bin/docker", "compose"]
        assert cmd[cmd.index("--file") + 1] == str(paths.compose_file)
        assert cmd.count("--env-file") == 2
        assert cmd[-1] == "ps"

    def test_compose_command_requires_docker(self, settings: CLISettings) -> None:
        with (
            patch(f"{MOD}.shutil.which", return_value=None),
            pytest.raises(RuntimeError, match="docker not found"),
        ):
            sc.compose_command(settings, "ps")

    def test_run_compose_returns_exit_code(self, settings: CLISettings) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=3)
        with (
            patch(f"{MOD}.shutil.which", return_value="/usr/bin/docker"),
            patch(f"{MOD}.subprocess.run", return_value=completed) as run,
        ):
            assert sc.run_compose(settings, "up", "-d") == 3
        assert run.call_args.args[0][-2:] == ["up", "-d"]


class TestHelpers:
    def test_detect_lan_ip_falls_back(self) -> None:
        with patch(f"{MOD}.socket.socket", side_effect=OSError("no network")):
            assert sc.detect_lan_ip() == "127.0.0.1"

    def test_detect_lan_ip_reads_socket_name(self) -> None:
        sock = MagicMock()
        sock.getsockname.return_value = ("192.168.1.9", 0)
        sock.__enter__.return_value = sock
        with patch(f"{MOD}.socket.socket", return_value=sock):
            assert sc.detect_lan_ip() == "192.168.1.9"

    def test_docker_socket_gid_missing(self, tmp_path: Path) -> None:
        assert sc.docker_socket_gid(str(tmp_path / "nope.sock")) is None

    def test_docker_socket_gid_present(self, tmp_path: Path) -> None:
        sock = tmp_path / "docker.sock"
        sock.write_text("")
        assert sc.docker_socket_gid(str(sock)) == sock.stat().st_gid

    def test_wait_for_health_success(self, settings: CLISettings) -> None:
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        clock = iter([0.0, 1.0, 2.0])
        with patch(f"{MOD}.urllib.request.urlopen", return_value=response):
            assert sc.wait_for_health(
                settings, timeout_seconds=10, sleep=lambda _s: None, clock=lambda: next(clock)
            )

    def test_wait_for_health_timeout(self, settings: CLISettings) -> None:
        ticks = iter([0.0, 5.0, 11.0])
        with patch(f"{MOD}.urllib.request.urlopen", side_effect=OSError("down")):
            assert not sc.wait_for_health(
                settings, timeout_seconds=10, sleep=lambda _s: None, clock=lambda: next(ticks)
            )

    def test_setup_url(self, settings: CLISettings) -> None:
        assert sc.setup_url(settings, "spark.local") == "http://spark.local:8080/setup"
