"""The installer registers the NVIDIA runtime with Docker on a host with a GPU."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install.sh"

# A fake `docker`: `info --format` answers the installer's two probes, and the
# NVIDIA runtime appears once the marker file exists.
FAKE_DOCKER = """\
printf "%s\\n" "$*" >> "{log}"
case "$1" in
  info)
    if [ "$2" = "--format" ] && [ "$3" = "{{{{.OperatingSystem}}}}" ]; then echo Ubuntu; exit 0; fi
    if [ "$2" = "--format" ]; then
      if [ -f "{marker}" ]; then
        echo '{{"nvidia": {{}}, "runc": {{}}}}'
      else
        echo '{{"runc": {{}}}}'
      fi
    fi
    exit 0 ;;
esac
exit 0
"""

# A fake `sudo`: records calls; `systemctl restart docker` makes the runtime
# visible (touches the marker) the way a real restart would; `apt-get install`
# leaves an `nvidia-ctk` on PATH the way the real toolkit package would.
FAKE_SUDO = """\
printf "%s\\n" "$*" >> "{log}"
{refuse}
case "$1" in -n) exit 0;; tee|gpg) cat >/dev/null; exit 0;; esac
if [ "$1" = apt-get ] && [ "$2" = install ]; then
  printf '#!/bin/sh\\nexit 0\\n' > "{ctk}"; chmod +x "{ctk}"
fi
if [ "$1" = systemctl ]; then {on_restart} "{marker}"; fi
exit 0
"""


def _tool(bin_dir: Path, name: str, body: str) -> Path:
    tool = bin_dir / name
    tool.write_text("#!/bin/sh\n" + body)
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    return tool


class Host:
    """A fake host on PATH: docker, nvidia-smi, nvidia-ctk, sudo, systemctl."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        gpu: bool,
        runtime: bool,
        ctk: bool,
        sudo_ok: bool,
        restart_registers: bool = True,
    ):
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.data = tmp_path / "data"
        self.marker = self.bin / "runtime-registered"
        self.sudo_log = self.bin / "sudo.log"
        if runtime:
            self.marker.touch()
        _tool(
            self.bin,
            "docker",
            FAKE_DOCKER.format(log=self.bin / "docker.log", marker=self.marker),
        )
        if gpu:
            _tool(self.bin, "nvidia-smi", "echo 'GPU 0: NVIDIA GB10'\n")
        if ctk:
            _tool(self.bin, "nvidia-ctk", "exit 0\n")
        _tool(
            self.bin,
            "sudo",
            FAKE_SUDO.format(
                log=self.sudo_log,
                refuse="" if sudo_ok else "exit 1",
                ctk=self.bin / "nvidia-ctk",
                on_restart="touch" if restart_registers else "true",
                marker=self.marker,
            ),
        )
        _tool(self.bin, "systemctl", "exit 0\n")

    def run(self) -> subprocess.CompletedProcess:
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
            },
            check=False,
        )

    def sudo_calls(self) -> list[str]:
        return self.sudo_log.read_text().splitlines() if self.sudo_log.exists() else []


def test_registers_the_runtime_when_the_gpu_cannot_reach_docker(tmp_path: Path) -> None:
    host = Host(tmp_path, gpu=True, runtime=False, ctk=True, sudo_ok=True)
    result = host.run()
    assert result.returncode == 0, result.stderr
    assert host.sudo_calls() == [
        "-n true",
        "nvidia-ctk runtime configure --runtime=docker",
        "systemctl restart docker",
    ]
    assert "NVIDIA runtime registered." in result.stderr
    assert (host.home / ".local" / "bin" / "niuu").exists()


def test_nothing_to_do_when_docker_already_has_the_runtime(tmp_path: Path) -> None:
    host = Host(tmp_path, gpu=True, runtime=True, ctk=True, sudo_ok=True)
    result = host.run()
    assert result.returncode == 0, result.stderr
    assert host.sudo_calls() == []
    assert "Docker has the NVIDIA runtime" in result.stderr


def test_installs_the_toolkit_first_when_nvidia_ctk_is_missing(tmp_path: Path) -> None:
    host = Host(tmp_path, gpu=True, runtime=False, ctk=False, sudo_ok=True)
    _tool(host.bin, "curl", "echo 'deb https://nvidia.github.io/stable/deb/$(ARCH) /'\n")
    _tool(host.bin, "gpg", "cat >/dev/null\nexit 0\n")
    _tool(host.bin, "apt-get", "exit 0\n")
    result = host.run()
    assert result.returncode == 0, result.stderr
    calls = host.sudo_calls()
    assert any(call.startswith("gpg --dearmor") for call in calls), calls
    assert any(call.startswith("tee /etc/apt/sources.list.d/nvidia-") for call in calls)
    assert "apt-get update" in calls
    assert "apt-get install -y nvidia-container-toolkit" in calls
    assert "nvidia-ctk runtime configure --runtime=docker" in calls
    assert calls[-1] == "systemctl restart docker"


def test_without_sudo_it_stops_with_the_command(tmp_path: Path) -> None:
    host = Host(tmp_path, gpu=True, runtime=False, ctk=True, sudo_ok=False)
    result = host.run()
    assert result.returncode == 1
    assert "sudo nvidia-ctk runtime configure --runtime=docker" in result.stderr
    assert "sudo systemctl restart docker" in result.stderr
    assert not (host.home / ".local" / "bin" / "niuu").exists()


def test_a_restart_that_does_not_register_fails_loudly(tmp_path: Path) -> None:
    host = Host(tmp_path, gpu=True, runtime=False, ctk=True, sudo_ok=True, restart_registers=False)
    result = host.run()
    assert result.returncode == 1
    assert "still reports no NVIDIA runtime" in result.stderr


def test_no_gpu_means_no_runtime_work(tmp_path: Path) -> None:
    host = Host(tmp_path, gpu=False, runtime=False, ctk=False, sudo_ok=False)
    result = host.run()
    assert result.returncode == 0, result.stderr
    assert host.sudo_calls() == []
    assert "NVIDIA" not in result.stderr
