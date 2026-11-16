"""A Mac session's long-running agents are launchd jobs that must actually run.

launchd runs ``ProgramArguments[0]`` as given and never searches the job's own PATH: a
bare command name exits 78 (EX_CONFIG) on every relaunch, quietly. The credential agent
was such a job, so a Mac session's ServiceAccount token was never refreshed and the
session lost Forge after an hour. ``START`` now names each agent's program by its path,
checks the job runs, and replaces a loaded job that does not.

These run ``START``'s agent step against a ``launchctl`` that keeps job state in files.
"""

import json
import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from volundr.adapters.outbound import darwin_guest_programs as programs
from volundr.adapters.outbound.ssh_guest_access import SshGuestAccess
from volundr.adapters.outbound.vm_slot_supervisor import DETAIL_PREFIX, STAGE_PREFIX
from volundr.domain.vm_runtime import VmRuntimeStageError

LABEL = "world.niuu.slot.0"
AGENT = f"{LABEL}.niuu-guest-agent"

_LAUNCHCTL = """#!/bin/sh
state={state}
echo "$*" >> "$state/calls"
case "$1" in
  print)
    name="${{2#system/}}"
    [ -f "$state/$name.loaded" ] || exit 113
    if [ -f "$state/$name.runs" ]; then
      echo "	pid = 4242"
    else
      echo "	last exit code = 78: EX_CONFIG"
    fi
    ;;
  bootstrap)
    name=$(basename "$3" .plist)
    cp "$3" "$state/$name.plist"
    touch "$state/$name.loaded"
    [ -n "$AGENT_RUNS" ] && touch "$state/$name.runs"
    ;;
  bootout)
    name="${{2#system/}}"
    rm -f "$state/$name.loaded" "$state/$name.runs"
    ;;
esac
exit 0
"""


class Mac:
    """A slot's directories, a fake ``launchctl``, and the bundle's agent program."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / "state"
        self.bin = root / "bin"
        self.bundle = root / "bundle"
        for directory in (self.state, self.bin, self.bundle):
            directory.mkdir()
        launchctl = self.bin / "launchctl"
        launchctl.write_text(_LAUNCHCTL.format(state=shlex.quote(str(self.state))))
        launchctl.chmod(0o755)

    def add_python(self) -> Path:
        python = self.bundle / "python"
        python.write_text("#!/bin/sh\nexit 0\n")
        python.chmod(0o755)
        return python

    def start(self, *, command: str = "python", runs: bool = True) -> subprocess.CompletedProcess:
        program = (
            programs.START.replace("'/Library/LaunchDaemons/'", repr(str(self.state) + "/"))
            # Handing the job's plist to root needs root; launchd's view is what is tested.
            .replace("os.chown(str(temporary), 0, 0)", "pass")
        ).replace(
            # Stop after the agents: the rest is the workspace and the session's own job.
            "# After the agents, as on Linux",
            "sys.exit(0)\n# After the agents, as on Linux",
        )
        payload = {
            "slot": {"uid": 61000, "data_root": str(self.root), "marker": "m"},
            "darwin": {
                "label": LABEL,
                "nix": False,
                "links": [],
                "state_root": str(self.root / "jobs"),
                "log_root": str(self.root / "logs"),
                "native_path": f"{self.bundle}:/usr/bin:/bin",
                "agent_home": str(self.root / "agent-home"),
                "agent_tmp": str(self.root / "agent-tmp"),
                "stop_timeout": 0.2,
                "poll": 0.02,
                "start_timeout": 0.3,
            },
            "sidecars": [
                {
                    "name": "niuu-guest-agent",
                    "command": [command, "-m", "volundr.guest_agent", "serve"],
                    "args": ["--config", "/x.json"],
                    "environment": {},
                }
            ],
        }
        environment = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}
        if runs:
            environment["AGENT_RUNS"] = "1"
        return subprocess.run(
            [sys.executable, "-I", "-c", program],
            input=json.dumps(payload).encode(),
            capture_output=True,
            env=environment,
        )

    def job(self) -> dict:
        return plistlib.loads((self.state / f"{AGENT}.plist").read_bytes())

    def calls(self) -> list[str]:
        return (self.state / "calls").read_text().splitlines()


@pytest.fixture
def mac(tmp_path) -> Mac:
    return Mac(tmp_path)


def _stage_error(completed: subprocess.CompletedProcess) -> VmRuntimeStageError:
    with pytest.raises(VmRuntimeStageError) as raised:
        SshGuestAccess._raise_command_error(
            completed.returncode,
            completed.stderr,
            safe_error_prefix=STAGE_PREFIX,
            safe_detail_prefix=DETAIL_PREFIX,
        )
    return raised.value


def test_a_bare_agent_command_becomes_its_path_on_the_jobs_path(mac):
    python = mac.add_python()

    completed = mac.start()

    assert completed.returncode == 0, completed.stderr
    assert mac.job()["ProgramArguments"] == [
        str(python),
        "-m",
        "volundr.guest_agent",
        "serve",
        "--config",
        "/x.json",
    ]


def test_an_agent_whose_program_is_on_no_path_fails_the_start(mac):
    error = _stage_error(mac.start())

    assert error.stage == "host-agents"
    assert "runs python, which is not an executable on its PATH" in error.diagnostic
    assert not (mac.state / f"{AGENT}.plist").exists()


def test_an_agent_that_launchd_cannot_keep_running_fails_the_start(mac):
    mac.add_python()

    error = _stage_error(mac.start(runs=False))

    assert error.stage == "host-agents"
    assert "niuu-guest-agent is not running" in error.diagnostic
    assert "last exit code: 78: EX_CONFIG" in error.diagnostic


def test_a_loaded_agent_that_is_not_running_is_replaced(mac):
    mac.add_python()
    (mac.state / f"{AGENT}.loaded").touch()

    completed = mac.start()

    assert completed.returncode == 0, completed.stderr
    assert f"bootout system/{AGENT}" in mac.calls()
    assert any(call.startswith("bootstrap system") for call in mac.calls())


def test_a_running_agent_is_kept(mac):
    mac.add_python()
    (mac.state / f"{AGENT}.loaded").touch()
    (mac.state / f"{AGENT}.runs").touch()

    completed = mac.start()

    assert completed.returncode == 0, completed.stderr
    assert not any(call.startswith(("bootout", "bootstrap")) for call in mac.calls())
