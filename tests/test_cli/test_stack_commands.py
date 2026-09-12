"""Tests for docker-mode CLI commands: up/down/status/doctor and platform init."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
import yaml
from typer.testing import CliRunner

from cli.app import build_app
from cli.commands import stack
from cli.commands.platform import _build_init_config, _prompt_mode_selection
from cli.config import CLISettings
from cli.registry import PluginRegistry
from cli.services.docker_host import GpuFacts, HostFacts
from cli.services.preflight import PreflightResult

runner = CliRunner()
MOD = "cli.commands.stack"


def _facts(tmp_path: Path) -> HostFacts:
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
        data_dir=str(tmp_path),
        disk_free_bytes=3 * 1024**4,
        disk_total_bytes=4 * 1024**4,
    )


@pytest.fixture
def settings(tmp_path: Path) -> CLISettings:
    return CLISettings(
        mode="docker",
        docker={"data_dir": str(tmp_path / "data"), "compose_dir": str(tmp_path / "bundle")},
    )


OK = PreflightResult(name="x", passed=True, message="ok")
FAIL = PreflightResult(name="gpu", passed=False, message="no gpu")


class TestStackUp:
    def test_happy_path_prints_setup_url(self, settings: CLISettings, tmp_path: Path) -> None:
        with (
            patch(f"{MOD}.run_docker_preflight_checks", return_value=[OK]),
            patch(f"{MOD}.collect_host_facts", return_value=_facts(tmp_path)),
            patch(f"{MOD}.detect_lan_ip", return_value="10.0.0.5"),
            patch(f"{MOD}.write_bundle") as write_bundle,
            patch(f"{MOD}.pull_applier_image", return_value=""),
            patch(f"{MOD}.run_compose", return_value=0) as run_compose,
            patch(f"{MOD}.wait_for_health", return_value=True),
        ):
            write_bundle.return_value = MagicMock(compose_dir=tmp_path / "bundle")
            stack.stack_up(settings)
        run_compose.assert_called_once_with(settings, "up", "--detach", "--remove-orphans")
        assert write_bundle.call_args.kwargs["external_host"] == "10.0.0.5"

    def test_up_merges_wizard_overrides_and_records_stack_file(
        self, settings: CLISettings, tmp_path: Path
    ) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "stack-overrides.yaml").write_text("docker:\n  bind_host: 0.0.0.0\n")
        with (
            patch(f"{MOD}.run_docker_preflight_checks", return_value=[OK]),
            patch(f"{MOD}.collect_host_facts", return_value=_facts(tmp_path)) as facts,
            patch(f"{MOD}.detect_lan_ip", return_value="10.0.0.5"),
            patch(f"{MOD}.write_bundle") as write_bundle,
            patch(f"{MOD}.pull_applier_image", return_value="pull failed"),
            patch(f"{MOD}.run_compose", return_value=0),
            patch(f"{MOD}.wait_for_health", return_value=True),
        ):
            write_bundle.return_value = MagicMock(compose_dir=tmp_path / "bundle")
            stack.stack_up(settings)
        used = write_bundle.call_args.args[0]
        assert used.docker.bind_host == "0.0.0.0"
        assert facts.call_args.kwargs["bind_host"] == "0.0.0.0"
        assert facts.call_args.kwargs["checks"] == [
            {"name": "x", "passed": True, "warn_only": False, "message": "ok"}
        ]
        recorded = yaml.safe_load((data / "stack.yaml").read_text())
        assert recorded["docker"]["bind_host"] == "0.0.0.0"

    def test_preflight_failure_aborts(self, settings: CLISettings) -> None:
        with (
            patch(f"{MOD}.run_docker_preflight_checks", return_value=[FAIL]),
            patch(f"{MOD}.run_compose") as run_compose,
            pytest.raises(typer.Exit) as exc,
        ):
            stack.stack_up(settings)
        assert exc.value.exit_code == 1
        run_compose.assert_not_called()

    def test_skip_preflight(self, settings: CLISettings, tmp_path: Path) -> None:
        with (
            patch(f"{MOD}.run_docker_preflight_checks", return_value=[FAIL]) as checks,
            patch(f"{MOD}.collect_host_facts", return_value=_facts(tmp_path)),
            patch(f"{MOD}.write_bundle", return_value=MagicMock(compose_dir=tmp_path)),
            patch(f"{MOD}.pull_applier_image", return_value=""),
            patch(f"{MOD}.run_compose", return_value=0) as compose,
            patch(f"{MOD}.wait_for_health", return_value=True),
        ):
            stack.stack_up(settings, skip_preflight=True)
        # The checks still run (the wizard shows them) but a failure does not block.
        checks.assert_called_once()
        compose.assert_called_once()

    def test_compose_failure_exits_with_code(self, settings: CLISettings, tmp_path: Path) -> None:
        with (
            patch(f"{MOD}.run_docker_preflight_checks", return_value=[OK]),
            patch(f"{MOD}.collect_host_facts", return_value=_facts(tmp_path)),
            patch(f"{MOD}.write_bundle", return_value=MagicMock(compose_dir=tmp_path)),
            patch(f"{MOD}.pull_applier_image", return_value=""),
            patch(f"{MOD}.run_compose", return_value=17),
            pytest.raises(typer.Exit) as exc,
        ):
            stack.stack_up(settings)
        assert exc.value.exit_code == 17

    def test_health_timeout_exits(self, settings: CLISettings, tmp_path: Path) -> None:
        with (
            patch(f"{MOD}.run_docker_preflight_checks", return_value=[OK]),
            patch(f"{MOD}.collect_host_facts", return_value=_facts(tmp_path)),
            patch(f"{MOD}.write_bundle", return_value=MagicMock(compose_dir=tmp_path)),
            patch(f"{MOD}.pull_applier_image", return_value=""),
            patch(f"{MOD}.run_compose", return_value=0),
            patch(f"{MOD}.wait_for_health", return_value=False),
            pytest.raises(typer.Exit) as exc,
        ):
            stack.stack_up(settings)
        assert exc.value.exit_code == 1

    def test_uses_configured_external_host(self, settings: CLISettings, tmp_path: Path) -> None:
        settings.server.external_host = "spark.local"
        with (
            patch(f"{MOD}.run_docker_preflight_checks", return_value=[OK]),
            patch(f"{MOD}.collect_host_facts", return_value=_facts(tmp_path)),
            patch(f"{MOD}.write_bundle", return_value=MagicMock(compose_dir=tmp_path)) as wb,
            patch(f"{MOD}.run_compose", return_value=0),
            patch(f"{MOD}.wait_for_health", return_value=True),
        ):
            stack.stack_up(settings)
        assert wb.call_args.kwargs["external_host"] == "spark.local"


class TestDownStatus:
    def test_down_without_bundle(self, settings: CLISettings) -> None:
        with patch(f"{MOD}.run_compose") as run_compose:
            stack.stack_down(settings)
        run_compose.assert_not_called()

    def test_down_with_bundle(self, settings: CLISettings) -> None:
        compose_file = stack.bundle_paths(settings).compose_file
        compose_file.parent.mkdir(parents=True)
        compose_file.write_text("services: {}\n")
        with (
            patch(f"{MOD}.run_compose", return_value=0) as run_compose,
            patch(f"{MOD}.remove_session_containers", return_value=2) as remove,
        ):
            stack.stack_down(settings)
        run_compose.assert_called_once_with(settings, "down")
        remove.assert_called_once_with(settings)

    def test_down_propagates_failure(self, settings: CLISettings) -> None:
        compose_file = stack.bundle_paths(settings).compose_file
        compose_file.parent.mkdir(parents=True)
        compose_file.write_text("services: {}\n")
        with (
            patch(f"{MOD}.run_compose", return_value=2),
            patch(f"{MOD}.remove_session_containers", return_value=0),
            pytest.raises(typer.Exit),
        ):
            stack.stack_down(settings)

    def test_status_before_start(self, settings: CLISettings) -> None:
        with patch(f"{MOD}.run_compose") as run_compose:
            stack.stack_status(settings)
        run_compose.assert_not_called()

    def test_status_runs_ps(self, settings: CLISettings) -> None:
        compose_file = stack.bundle_paths(settings).compose_file
        compose_file.parent.mkdir(parents=True)
        compose_file.write_text("services: {}\n")
        with patch(f"{MOD}.run_compose", return_value=0) as run_compose:
            stack.stack_status(settings)
        run_compose.assert_called_once_with(settings, "ps")

    def test_status_propagates_failure(self, settings: CLISettings) -> None:
        compose_file = stack.bundle_paths(settings).compose_file
        compose_file.parent.mkdir(parents=True)
        compose_file.write_text("services: {}\n")
        with patch(f"{MOD}.run_compose", return_value=1), pytest.raises(typer.Exit):
            stack.stack_status(settings)


class TestDoctor:
    def test_docker_mode_reports_facts_and_checks(
        self, settings: CLISettings, tmp_path: Path
    ) -> None:
        with (
            patch(f"{MOD}.collect_host_facts", return_value=_facts(tmp_path)),
            patch(f"{MOD}.run_docker_preflight_checks", return_value=[OK]),
        ):
            assert stack.run_doctor(settings) is True

    def test_docker_mode_failure(self, settings: CLISettings, tmp_path: Path) -> None:
        with (
            patch(f"{MOD}.collect_host_facts", return_value=_facts(tmp_path)),
            patch(f"{MOD}.run_docker_preflight_checks", return_value=[FAIL]),
        ):
            assert stack.run_doctor(settings) is False

    def test_mini_mode_uses_platform_preflight(self) -> None:
        with patch("cli.services.preflight.run_preflight_checks", return_value=[OK]):
            assert stack.run_doctor(CLISettings(mode="mini")) is True

    def test_preflight_config_from_settings(self, settings: CLISettings) -> None:
        settings.docker.min_disk_space_gib = 7
        settings.docker.require_gpu = False
        config = stack.docker_preflight_config(settings)
        assert config.min_disk_space_bytes == 7 * 1024**3
        assert config.require_gpu is False
        assert config.ports == [8080]


class TestTopLevelCommands:
    def _app(self, settings: CLISettings):
        return build_app(settings=settings, registry=PluginRegistry())

    def test_up_docker_mode_dispatches(self, settings: CLISettings) -> None:
        with patch(f"{MOD}.stack_up") as stack_up:
            result = runner.invoke(self._app(settings), ["up"])
        assert result.exit_code == 0, result.output
        stack_up.assert_called_once()
        assert stack_up.call_args.kwargs["skip_preflight"] is False

    def test_up_mode_override(self) -> None:
        with patch(f"{MOD}.stack_up") as stack_up:
            result = runner.invoke(
                self._app(CLISettings(mode="mini")), ["up", "--mode", "docker", "--skip-preflight"]
            )
        assert result.exit_code == 0, result.output
        assert stack_up.call_args.args[0].mode == "docker"
        assert stack_up.call_args.kwargs["skip_preflight"] is True

    def test_up_non_docker_forwards_to_platform(self) -> None:
        with patch("cli.commands.platform._startup", side_effect=typer.Exit(0)) as startup:
            result = runner.invoke(self._app(CLISettings(mode="mini")), ["up"])
        assert result.exit_code == 0, result.output
        startup.assert_called_once()

    def test_down_and_status_forward(self, settings: CLISettings) -> None:
        with (
            patch(f"{MOD}.stack_down") as down,
            patch(f"{MOD}.stack_status") as status,
        ):
            assert runner.invoke(self._app(settings), ["down"]).exit_code == 0
            assert runner.invoke(self._app(settings), ["status"]).exit_code == 0
        down.assert_called_once()
        status.assert_called_once()

    def test_platform_up_docker_mode_dispatches(self, settings: CLISettings) -> None:
        with patch(f"{MOD}.stack_up") as stack_up:
            result = runner.invoke(self._app(settings), ["platform", "up", "--skip-preflight"])
        assert result.exit_code == 0, result.output
        assert stack_up.call_args.kwargs["skip_preflight"] is True

    def test_doctor_exit_codes(self, settings: CLISettings) -> None:
        with patch(f"{MOD}.run_doctor", return_value=True):
            assert runner.invoke(self._app(settings), ["doctor"]).exit_code == 0
        with patch(f"{MOD}.run_doctor", return_value=False):
            assert runner.invoke(self._app(settings), ["doctor"]).exit_code == 1

    def test_doctor_mode_override(self) -> None:
        with patch(f"{MOD}.run_doctor", return_value=True) as run_doctor:
            runner.invoke(self._app(CLISettings(mode="mini")), ["doctor", "--mode", "docker"])
        assert run_doctor.call_args.args[0].mode == "docker"


class TestInit:
    def test_prompt_accepts_docker(self) -> None:
        with patch("cli.commands.platform.typer.prompt", return_value="4"):
            assert _prompt_mode_selection() == "docker"
        with patch("cli.commands.platform.typer.prompt", return_value="docker"):
            assert _prompt_mode_selection() == "docker"

    def test_init_config_for_docker_round_trips(self) -> None:
        config = _build_init_config("docker")
        assert config["mode"] == "docker"
        assert config["docker"]["data_dir"] == "/var/lib/niuu"
        assert "vllm" not in config["docker"]
        loaded = CLISettings(**yaml.safe_load(yaml.safe_dump(config)))
        assert loaded.mode == "docker"
        assert loaded.docker.require_gpu is False
