"""The installer: docker mode from the image, mini mode from the release binary."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install.sh"


def _fake_docker(
    bin_dir: Path,
    *,
    runtimes: dict | None = None,
    info_ok: bool = True,
    operating_system: str = "Ubuntu 24.04.5 LTS",
    architecture: str = "amd64",
    image_architecture: str | None = None,
    endpoint: str = "unix:///var/run/docker.sock",
) -> Path:
    """A `docker` on PATH that records every call and answers the installer's probes."""
    uname = bin_dir / "uname"
    if not uname.exists():
        uname.write_text('#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; esac\n')
        uname.chmod(0o755)
    # The simulated daemon's socket must not depend on the host running Docker
    # at the same Linux path (macOS commonly uses a context-specific socket).
    fake_stat = bin_dir / "stat"
    fake_stat.write_text('#!/bin/sh\ncase "$1 $2" in "-c %g"|"-f %g") echo 0;; *) exit 1;; esac\n')
    fake_stat.chmod(0o755)
    log = bin_dir / "docker.log"
    info = json.dumps(runtimes if runtimes is not None else {})
    script = f"""#!/bin/sh
printf '%s\\n' "$*" >> "{log}"
case "$1" in
  info)
    {"" if info_ok else "exit 1"}
    if [ "$2" = "--format" ] && [ "$3" = "{{{{.OperatingSystem}}}}" ]; then
      printf '%s\\n' '{operating_system}'; exit 0
    fi
    if [ "$2" = "--format" ] && [ "$3" = "{{{{.Architecture}}}}" ]; then
      echo '{architecture}'; exit 0
    fi
    if [ "$2" = "--format" ]; then printf '%s\\n' '{info}'; fi
    exit 0 ;;
  context) echo '{endpoint}'; exit 0 ;;
  image) echo '{image_architecture or architecture}'; exit 0 ;;
  compose) exit 0 ;;
  pull) exit 0 ;;
  run) case "$*" in *"--entrypoint stat"*) echo 0 ;; esac; exit 0 ;;
esac
exit 0
"""
    docker = bin_dir / "docker"
    docker.write_text(script)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    return log


def _run(
    tmp_path: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    full_env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "NIUU_DATA_DIR": str(tmp_path / "data"),
        "NIUU_INSTALL_DIR": str(home / ".local" / "bin"),
        **(env or {}),
    }
    return subprocess.run(
        ["sh", str(INSTALLER), *args],
        capture_output=True,
        text=True,
        env=full_env,
        cwd=tmp_path,
        check=False,
    )


class TestModeFlag:
    def test_unknown_mode_and_option_are_refused(self, tmp_path: Path) -> None:
        bad_mode = _run(tmp_path, "--mode", "cloud")
        assert bad_mode.returncode == 1
        assert "unknown mode 'cloud'" in bad_mode.stderr
        bad_option = _run(tmp_path, "--fast")
        assert bad_option.returncode == 1
        assert "unknown option: --fast" in bad_option.stderr

    def test_mini_mode_downloads_a_release_binary(self, tmp_path: Path) -> None:
        """Without network the download fails, which proves the mode was selected."""
        (tmp_path / "bin").mkdir()
        curl = tmp_path / "bin" / "curl"
        curl.write_text("#!/bin/sh\nexit 22\n")
        curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
        result = _run(tmp_path, "--mode=mini", env={"NIUU_VERSION": "v9.9.9"})
        assert result.returncode == 1
        assert "download failed" in result.stderr
        assert "releases/download/v9.9.9/niuu-" in result.stderr


class TestDockerMode:
    def test_colima_uses_daemon_arch_socket_and_native_lan(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name, script in {
            "uname": 'case "$1" in -s) echo Darwin;; -m) echo arm64;; esac',
            "route": "echo 'interface: en0'",
            "ipconfig": 'test "$*" = "getifaddr en0" && echo 192.168.1.87',
        }.items():
            executable = bin_dir / name
            executable.write_text(f"#!/bin/sh\n{script}\n")
            executable.chmod(0o755)
        log = _fake_docker(
            bin_dir,
            endpoint="unix:///Users/test/.colima/default/docker.sock",
            architecture="x86_64",
            image_architecture="amd64",
            runtimes={"nvidia": {}, "runc": {}},
        )
        result = _run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert "host is arm64 but Docker runs amd64" in result.stderr
        assert "NVIDIA" not in result.stderr
        calls = log.read_text()
        assert "pull -q --platform linux/amd64" in calls
        call = [line for line in calls.splitlines() if line.startswith("run ")][-1]
        assert "--platform linux/amd64" in call
        assert "-v /var/run/docker.sock:/var/run/docker.sock" in call
        assert ".colima" not in call
        assert "/etc/os-release" not in call
        assert "--group-add 0" in call
        assert "-e NIUU_DOCKER__HOST_LAN_IP " in call
        assert "-e NIUU_DOCKER__HOST_ARCH " in call
        assert "--gpus" not in call

    def test_rejects_an_image_for_a_different_architecture(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _fake_docker(bin_dir, architecture="amd64", image_architecture="arm64")
        result = _run(tmp_path, env={"NIUU_NO_PULL": "1"})
        assert result.returncode == 1
        assert "is arm64 but Docker runs amd64" in result.stderr
        assert not (tmp_path / "home" / ".local" / "bin" / "niuu").exists()

    def test_honors_a_rootless_docker_context_socket(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _fake_docker(bin_dir, endpoint="unix:///run/user/1000/docker.sock")
        result = _run(tmp_path, env={"NIUU_NO_UP": "1"})
        assert result.returncode == 0, result.stderr
        wrapper = (tmp_path / "home" / ".local" / "bin" / "niuu").read_text()
        assert 'SOCKET="/run/user/1000/docker.sock"' in wrapper

    def test_installs_a_wrapper_that_runs_the_cli_from_the_image(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = _fake_docker(bin_dir, runtimes={"nvidia": {}, "runc": {}})
        result = _run(
            tmp_path, env={"NIUU_IMAGE_TAG": "spark-onboarding-wizard", "NIUU_NO_UP": "1"}
        )
        assert result.returncode == 0, result.stderr
        assert (
            "pull -q --platform linux/amd64 ghcr.io/niuulabs/niuu:spark-onboarding-wizard"
            in log.read_text()
        )
        wrapper = tmp_path / "home" / ".local" / "bin" / "niuu"
        assert wrapper.exists()
        assert os.access(wrapper, os.X_OK)
        text = wrapper.read_text()
        assert 'IMAGE="ghcr.io/niuulabs/niuu:spark-onboarding-wizard"' in text
        assert 'SKULD_IMAGE="ghcr.io/niuulabs/skuld:spark-onboarding-wizard"' in text
        assert f'DATA_DIR="{tmp_path / "data"}"' in text
        assert (tmp_path / "data").is_dir()
        assert "Run 'niuu up' to start the platform." in result.stderr

        # The wrapper runs the CLI in a container with the host's identity, Docker
        # socket, config dir, data dir and network, and the GPU when Docker has it.
        log.write_text("")
        run = subprocess.run(
            ["sh", str(wrapper), "up", "--skip-preflight"],
            capture_output=True,
            text=True,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home")},
            check=False,
        )
        assert run.returncode == 0, run.stderr
        call = [line for line in log.read_text().splitlines() if line.startswith("run ")][-1]
        assert "--gpus all" in call
        assert "--network host" in call
        assert f"--user {os.getuid()}:{os.getgid()}" in call
        assert "-e NIUU_MODE=docker" in call
        assert "-e NIUU_DOCKER__IMAGE=ghcr.io/niuulabs/niuu:spark-onboarding-wizard" in call
        assert f"-v {tmp_path / 'data'}:{tmp_path / 'data'}" in call
        assert f"-v {tmp_path / 'home'}/.niuu:{tmp_path / 'home'}/.niuu" in call
        assert call.endswith(
            "--entrypoint /opt/venv/bin/niuu ghcr.io/niuulabs/niuu:spark-onboarding-wizard "
            "up --skip-preflight"
        )

    def test_docker_desktop_gets_roots_group_and_niuu_env_passes_through(
        self, tmp_path: Path
    ) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = _fake_docker(bin_dir, operating_system="Docker Desktop")
        assert _run(tmp_path, env={"NIUU_NO_UP": "1"}).returncode == 0
        wrapper = tmp_path / "home" / ".local" / "bin" / "niuu"
        subprocess.run(
            ["sh", str(wrapper), "status"],
            capture_output=True,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(tmp_path / "home"),
                "NIUU_SERVER__PORT": "8081",
                "NIUU_DOCKER__PROJECT_NAME": "trial",
            },
            check=False,
        )
        call = [line for line in log.read_text().splitlines() if line.startswith("run ")][-1]
        assert "--group-add 0 " in call
        assert "-e NIUU_SERVER__PORT " in call
        assert "-e NIUU_DOCKER__PROJECT_NAME " in call

    def test_no_pull_needs_the_image_locally(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = _fake_docker(bin_dir)
        result = _run(tmp_path, env={"NIUU_NO_UP": "1", "NIUU_NO_PULL": "1"})
        assert result.returncode == 0, result.stderr
        assert "pull" not in log.read_text()
        assert "image inspect ghcr.io/niuulabs/niuu:latest" in log.read_text()

    def test_no_gpu_flag_without_the_nvidia_runtime(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = _fake_docker(bin_dir, runtimes={"runc": {}})
        assert _run(tmp_path, env={"NIUU_NO_UP": "1"}).returncode == 0
        wrapper = tmp_path / "home" / ".local" / "bin" / "niuu"
        subprocess.run(
            ["sh", str(wrapper), "status"],
            capture_output=True,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home")},
            check=False,
        )
        call = [line for line in log.read_text().splitlines() if line.startswith("run ")][-1]
        assert "--gpus" not in call
        assert call.endswith(" status")

    def test_starts_the_platform_by_default(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = _fake_docker(bin_dir)
        result = _run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert [line for line in log.read_text().splitlines() if line.startswith("run ")][
            -1
        ].endswith(" up")

    def test_says_how_to_reach_docker_when_the_daemon_refuses(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _fake_docker(bin_dir, info_ok=False)
        result = _run(tmp_path)
        assert result.returncode == 1
        assert "daemon is not reachable" in result.stderr or "usermod -aG docker" in result.stderr
        assert not (tmp_path / "home" / ".local" / "bin" / "niuu").exists()

    def test_says_how_to_create_the_data_dir(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _fake_docker(bin_dir)
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        blocked.chmod(0o500)
        if os.access(blocked, os.W_OK):
            pytest.skip("running as root; the directory cannot be made unwritable")
        try:
            result = _run(tmp_path, env={"NIUU_DATA_DIR": str(blocked / "niuu")})
        finally:
            blocked.chmod(0o700)
        assert result.returncode == 1
        assert "sudo mkdir -p" in result.stderr
        assert "NIUU_DATA_DIR" in result.stderr

    def test_tells_a_linux_user_outside_the_docker_group_the_steps_in_order(
        self, tmp_path: Path
    ) -> None:
        if os.uname().sysname != "Linux":
            pytest.skip("the group check only applies on Linux")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _fake_docker(bin_dir, info_ok=False)
        fake_id = bin_dir / "id"
        fake_id.write_text(
            '#!/bin/sh\ncase "$1" in -nG) echo users;; -un) echo me;; *) exit 1;; esac\n'
        )
        fake_id.chmod(fake_id.stat().st_mode | stat.S_IEXEC)
        result = _run(tmp_path)
        assert result.returncode == 1
        lines = result.stderr.splitlines()
        assert "  1. sudo usermod -aG docker me" in lines
        assert lines.index("  1. sudo usermod -aG docker me") < lines.index(
            "  3. run this installer again"
        )

    def test_docker_is_required(self, tmp_path: Path) -> None:
        # CI runners ship Docker in /usr/bin; the installer must see a PATH
        # with everything else but no docker at all.
        tools = tmp_path / "tools"
        tools.mkdir()
        for directory in ("/usr/bin", "/bin"):
            for entry in Path(directory).iterdir():
                if entry.name == "docker" or (tools / entry.name).exists():
                    continue
                (tools / entry.name).symlink_to(entry)
        result = subprocess.run(
            ["sh", str(INSTALLER)],
            capture_output=True,
            text=True,
            env={
                "PATH": str(tools),
                "HOME": str(tmp_path / "home"),
                "NIUU_DATA_DIR": str(tmp_path / "data"),
                "NIUU_INSTALL_DIR": str(tmp_path / "home" / ".local" / "bin"),
            },
            cwd=tmp_path,
            check=False,
        )
        assert result.returncode == 1
        assert "Docker Engine and Compose are required" in result.stderr

    def test_writes_the_initial_config_once(self, tmp_path: Path) -> None:
        """The vLLM image and the wizard's model list are configuration the installer
        writes into ~/.niuu/config.yaml, never something baked into the platform image;
        a file that already exists is the user's and stays."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _fake_docker(bin_dir)
        result = _run(tmp_path, env={"NIUU_NO_UP": "1"})
        assert result.returncode == 0, result.stderr
        config = tmp_path / "home" / ".niuu" / "config.yaml"
        assert f"Wrote {config}." in result.stderr
        data = yaml.safe_load(config.read_text())
        assert data["mode"] == "docker"
        assert data["docker"]["vllm"]["image"].startswith("nvcr.io/nvidia/vllm:")
        models = {entry["id"]: entry for entry in data["docker"]["models"]}
        nemotron = models["nemotron-3-nano-30b"]
        assert nemotron["trust_remote_code"] is True
        assert nemotron["recommended"] is True
        assert "--tool-call-parser" in nemotron["serve_args"]
        # The model thinks before it answers; vLLM's built-in parser keeps the
        # thinking out of the reply the agent sees.
        assert nemotron["serve_args"][-2:] == ["--reasoning-parser", "nemotron_v3"]
        assert models["qwen3-coder-30b"]["weight_gib"] == 24

        config.write_text("mode: docker\ndocker:\n  vllm:\n    image: mine:1\n")
        again = _run(tmp_path, env={"NIUU_NO_UP": "1"})
        assert again.returncode == 0, again.stderr
        assert f"Keeping your existing {config}." in again.stderr
        assert yaml.safe_load(config.read_text())["docker"]["vllm"]["image"] == "mine:1"
