"""A GPU that Docker cannot use stops the installer with the fix; it never runs sudo."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install.sh"

FAKE_DOCKER = """\
case "$1" in
  context) echo unix:///var/run/docker.sock; exit 0 ;;
  image) echo amd64; exit 0 ;;
  info)
    if [ "$2" = "--format" ] && [ "$3" = "{{{{.OperatingSystem}}}}" ]; then echo Ubuntu; exit 0; fi
    if [ "$2" = "--format" ] && [ "$3" = "{{{{.Architecture}}}}" ]; then echo amd64; exit 0; fi
    if [ "$2" = "--format" ]; then echo '{runtimes}'; fi
    exit 0 ;;
esac
exit 0
"""


def _tool(bin_dir: Path, name: str, body: str) -> Path:
    tool = bin_dir / name
    tool.write_text("#!/bin/sh\n" + body)
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    return tool


class Host:
    """A fake host on PATH: docker, nvidia-smi, nvidia-ctk, and a sudo that must never run."""

    def __init__(self, tmp_path: Path, *, gpu: bool, runtime: bool, ctk: bool):
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        _tool(self.bin, "uname", 'case "$1" in -s) echo Linux;; -m) echo x86_64;; esac\n')
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.data = tmp_path / "data"
        self.sudo_log = self.bin / "sudo.log"
        runtimes = '{"nvidia": {}, "runc": {}}' if runtime else '{"runc": {}}'
        _tool(self.bin, "docker", FAKE_DOCKER.format(runtimes=runtimes))
        if gpu:
            _tool(self.bin, "nvidia-smi", "echo 'NVIDIA GB10'\n")
        if ctk:
            _tool(self.bin, "nvidia-ctk", "exit 0\n")
        _tool(self.bin, "sudo", f'printf "%s\\n" "$*" >> "{self.sudo_log}"\nexit 0\n')

    def run(self, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", str(INSTALLER)],
            capture_output=True,
            text=True,
            env={
                "PATH": f"{self.bin}:/usr/bin:/bin",
                "HOME": str(self.home),
                "NIUU_DATA_DIR": str(self.data),
                "NIUU_INSTALL_DIR": str(self.home / ".local" / "bin"),
                "NIUU_NO_UP": "1",
                **env,
            },
            check=False,
        )

    def sudo_ran(self) -> bool:
        return self.sudo_log.exists()

    def installed(self) -> bool:
        return (self.home / ".local" / "bin" / "niuu").exists()


def test_a_gpu_docker_cannot_use_stops_the_install_with_the_registration_command(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path, gpu=True, runtime=False, ctk=True)
    result = host.run()
    assert result.returncode == 1
    assert "GPU found (NVIDIA GB10)" in result.stderr
    assert "Container Toolkit is installed. Register it" in result.stderr
    assert "sudo nvidia-ctk runtime configure --runtime=docker" in result.stderr
    assert "sudo systemctl restart docker" in result.stderr
    assert "rerun this installer" in result.stderr
    assert not host.sudo_ran()
    assert not host.installed()


def test_without_the_toolkit_it_points_at_the_install_guide(tmp_path: Path) -> None:
    host = Host(tmp_path, gpu=True, runtime=False, ctk=False)
    result = host.run()
    assert result.returncode == 1
    assert "Install the NVIDIA Container Toolkit" in result.stderr
    assert "docs.nvidia.com" in result.stderr
    assert not host.sudo_ran()
    assert not host.installed()


def test_nothing_to_do_when_docker_already_has_the_runtime(tmp_path: Path) -> None:
    host = Host(tmp_path, gpu=True, runtime=True, ctk=True)
    result = host.run()
    assert result.returncode == 0, result.stderr
    assert "Docker has the NVIDIA runtime" in result.stderr
    assert not host.sudo_ran()
    assert host.installed()


def test_skipping_the_gpu_is_an_explicit_choice(tmp_path: Path) -> None:
    host = Host(tmp_path, gpu=True, runtime=False, ctk=True)
    result = host.run(NIUU_SKIP_GPU="1")
    assert result.returncode == 0, result.stderr
    assert not host.sudo_ran()
    assert host.installed()


def test_no_gpu_means_no_runtime_check(tmp_path: Path) -> None:
    host = Host(tmp_path, gpu=False, runtime=False, ctk=False)
    result = host.run()
    assert result.returncode == 0, result.stderr
    assert "NVIDIA" not in result.stderr
    assert not host.sudo_ran()
