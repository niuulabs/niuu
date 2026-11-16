"""A host-mode session clones a private repository with its attached Git integration.

The slot's agent renders the integration's token into its mounted volume, as its own
user. ``_HOST_START`` reads it as root and hands it to ``prepare_storage``, which clones
as the slot's user through a credential helper reading the token from the clone's
environment, then leaves the repository reading the token file for the session's own
Git. The token never enters the launch payload, a URL, an argument or the repository's
config.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_adapters.test_ssh_host_vm_runtime import _launch, _runtime
from tests.test_adapters.test_ssh_vm_runtime_git import (
    REPO,
    TOKEN_FILE,
    _git_values,
    _session,
    _spec,
)
from volundr.adapters.outbound import ssh_host_vm_runtime as module
from volundr.adapters.outbound import vm_slot_supervisor
from volundr.adapters.outbound.host_session_programs import SLOT_STORAGE
from volundr.adapters.outbound.ssh_guest_access import SshGuestAccess
from volundr.domain.compute import MachineBootstrap
from volundr.domain.vm_runtime import VmRuntimeStageError

TOKEN = "ghs_s3cr3t"


def test_a_host_session_is_told_which_mount_holds_the_token(tmp_path):
    runtime = _runtime(tmp_path)

    launch = _launch(runtime.bootstrap(_session(), _spec(_git_values()), MachineBootstrap()))

    assert launch["repo"] == REPO
    assert launch["git"]["token_mount"] == "/run/secrets"
    assert launch["git"]["token_path"] == "git/abc123/token"
    # The session's own Git reads the token file through the chart's credential helper.
    assert launch["environment"]["GIT_TOKEN_FILE"] == TOKEN_FILE
    assert launch["environment"]["GIT_CONFIG_KEY_0"] == f"credential.{REPO}.helper"
    assert TOKEN not in json.dumps(launch)


def _start(tmp_path: Path, *, rendered: bool) -> subprocess.CompletedProcess:
    """Run ``_HOST_START`` to its workspace step; ``prepare_storage`` prints its ``git``."""
    secrets = tmp_path / "secrets"
    if rendered:
        (secrets / "git" / "abc123").mkdir(parents=True)
        (secrets / "git" / "abc123" / "token").write_text(TOKEN + "\n")
    program = module._HOST_START.replace(
        "prepare_storage(root, s['marker']", "replaced_storage(root, s['marker']"
    ).replace(
        "stage = ['payload']",
        "def replaced_storage(*args):\n"
        "    print(json.dumps(args[6]))\n"
        "    sys.exit(0)\n"
        "stage = ['payload']",
    )
    payload = {
        "slot": {"netns": "niuu-slot-0", "data_root": str(tmp_path), "marker": "m", "uid": 0},
        "host": {
            "pidfile": str(tmp_path / "run" / "s.pid"),
            "keep_owners": [],
            "stop_timeout": 1,
            "poll": 0.01,
        },
        "repo": REPO,
        "branch": "main",
        "git": {
            "repo_url": REPO,
            "token_file": TOKEN_FILE,
            "token_mount": "/run/secrets",
            "token_path": "git/abc123/token",
            "username": "x-access-token",
            "user_name": "Ada",
            "user_email": "ada@example.test",
            "base_branch": "",
        },
        "secret_mounts": [],
        "volume_mounts": [{"source": str(secrets), "target": "/run/secrets"}],
    }
    return subprocess.run(
        [sys.executable, "-I", "-c", program],
        input=json.dumps(payload).encode(),
        capture_output=True,
    )


def test_the_start_reads_the_rendered_token_and_hands_it_to_the_clone(tmp_path):
    completed = _start(tmp_path, rendered=True)

    assert completed.returncode == 0, completed.stderr
    git = json.loads(completed.stdout)
    assert (git["username"], git["token"]) == ("x-access-token", TOKEN)
    settings = dict(git["settings"])
    # The session's Git reads the file at its own path, never the token itself.
    assert settings["niuu.gitTokenFile"] == TOKEN_FILE
    assert settings["credential.helper"] == ""
    assert "niuu.gitTokenFile" in settings[f"credential.{REPO}.helper"]
    assert settings["user.name"] == "Ada"
    assert "niuu.baseBranch" not in settings
    assert TOKEN not in json.dumps(git["settings"])


def test_a_token_that_was_not_rendered_fails_the_workspace_step(tmp_path):
    completed = _start(tmp_path, rendered=False)

    with pytest.raises(VmRuntimeStageError) as raised:
        SshGuestAccess._raise_command_error(
            completed.returncode,
            completed.stderr,
            safe_error_prefix=vm_slot_supervisor.STAGE_PREFIX,
            safe_detail_prefix=vm_slot_supervisor.DETAIL_PREFIX,
        )
    assert raised.value.stage == "host-workspace"
    assert raised.value.diagnostic == (
        f"The Git integration token was not rendered at {TOKEN_FILE}"
    )


def _storage(tmp_path: Path, monkeypatch) -> tuple[dict, list]:
    namespace: dict = {}
    exec(SLOT_STORAGE, namespace)
    # Giving the slot its directories needs root; what is under test is the Git calls.
    namespace["owned"] = lambda path, mode, uid: Path(path).mkdir(exist_ok=True)
    calls: list = []

    def run(args, **kwargs):
        calls.append((list(args), kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    (tmp_path / ".allocation").write_text("m")
    return namespace, calls


def test_prepare_storage_clones_as_the_slot_with_the_token_in_its_environment_only(
    tmp_path, monkeypatch
):
    namespace, calls = _storage(tmp_path, monkeypatch)
    git = {
        "username": "x-access-token",
        "token": TOKEN,
        "settings": [("niuu.gitTokenFile", TOKEN_FILE), ("user.name", "Ada")],
    }

    namespace["prepare_storage"](tmp_path, "m", os.getuid(), REPO, "main", (), git)

    clone, chown, *configs = calls
    args, kwargs = clone
    assert args[args.index("clone") :] == [
        "clone",
        "--branch",
        "main",
        "--",
        REPO,
        str(tmp_path / "workspace"),
    ]
    assert f"credential.{REPO}.helper=" in " ".join(args)
    assert TOKEN not in " ".join(args)
    assert kwargs["env"]["NIUU_GIT_TOKEN"] == TOKEN
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert (kwargs["user"], kwargs["extra_groups"]) == (os.getuid(), [])
    # The session's settings are written as the slot, and none of them is the token.
    config = str(tmp_path / "workspace" / ".git" / "config")
    assert [c[0] for c in configs] == [
        ["git", "config", "--file", config, "niuu.gitTokenFile", TOKEN_FILE],
        ["git", "config", "--file", config, "user.name", "Ada"],
    ]
    assert all("NIUU_GIT_TOKEN" not in c[1]["env"] for c in configs)
    assert chown[0][0] == "find"


def test_a_restored_repository_is_the_slots_before_its_git_settings_are_written(
    tmp_path, monkeypatch
):
    # A restart restores the stopped session's workspace as root, owners dropped, and
    # clones nothing: the slot can only write .git/config once the storage is handed over.
    namespace, calls = _storage(tmp_path, monkeypatch)
    (tmp_path / "workspace" / ".git").mkdir(parents=True)
    git = {"username": "x-access-token", "token": TOKEN, "settings": [("user.name", "Ada")]}

    namespace["prepare_storage"](tmp_path, "m", os.getuid(), REPO, "main", (), git)

    assert [c[0][:3] for c in calls] == [
        ["find", str(tmp_path), "-exec"],
        ["git", "config", "--file"],
    ]
    assert calls[1][1]["user"] == os.getuid()


def test_prepare_storage_without_an_integration_clones_plain_git(tmp_path, monkeypatch):
    namespace, calls = _storage(tmp_path, monkeypatch)

    namespace["prepare_storage"](tmp_path, "m", os.getuid(), REPO, "main")

    (args, kwargs), chown = calls
    assert args == ["git", "clone", "--branch", "main", "--", REPO, str(tmp_path / "workspace")]
    assert "NIUU_GIT_TOKEN" not in kwargs["env"]
    assert chown[0][0] == "find"
