"""A VM container session clones a private repository with its attached Git integration.

The Git contributor names the integration token's file inside the session's containers;
the secret injection's guest agent renders it into a mounted volume. Forge tells the
guest's start program which mount holds it, the clone reads it through a credential
helper, and the session's own Git keeps reading it from the container path. The token
itself never enters the launch payload, a URL or the repository's config.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from volundr.adapters.outbound import ssh_vm_runtime as module
from volundr.adapters.outbound import vm_slot_supervisor
from volundr.adapters.outbound.ssh_guest_access import SshGuestAccess
from volundr.adapters.outbound.ssh_vm_runtime import HostModeRuntime, SshContainerVmRuntime
from volundr.domain.compute import MachineBootstrap
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec
from volundr.domain.services.host_access_key import generate_host_access_key
from volundr.domain.vm_runtime import VmRuntimeStageError

HOST_KEY = generate_host_access_key()
TOKEN_FILE = "/run/secrets/git/abc123/token"
REPO = "https://github.com/acme/private.git"


def _git_values(token_file: str = TOKEN_FILE) -> dict:
    return {
        "repoUrl": REPO,
        "cloneUrl": REPO,
        "branch": "main",
        "baseBranch": "main",
        "credentials": {"secretName": "", "tokenFile": token_file, "username": "x-access-token"},
        "userName": "ada@example.test",
        "userEmail": "ada@example.test",
    }


def _spec(git: dict | None) -> SessionSpec:
    values: dict = {"session": {}}
    if git is not None:
        values["git"] = git
    return SessionSpec(
        values=values,
        pod_spec=PodSpecAdditions(
            volumes=({"name": "openbao-secrets", "emptyDir": {}},),
            volume_mounts=(
                {"name": "openbao-secrets", "mountPath": "/run/secrets", "readOnly": True},
            ),
        ),
    )


def _session() -> Session:
    return Session(
        name="private",
        owner_id="ada",
        tenant_id="acme",
        source=GitSource(repo="https://github.com/acme/private", branch="main"),
    )


def _launch(runtime: SshContainerVmRuntime, spec: SessionSpec) -> dict:
    bootstrap = runtime.bootstrap(_session(), spec, MachineBootstrap())
    return json.loads(next(f.content for f in bootstrap.files if f.path == module._LAUNCH))


def _runtime(tmp_path: Path, cls: type = SshContainerVmRuntime) -> SshContainerVmRuntime:
    return cls(host_key=HOST_KEY, data_dir=str(tmp_path / "data"), skuld_image="test/skuld:dev")


class TestLaunchPayload:
    def test_the_clone_is_told_which_mount_holds_the_token(self, tmp_path):
        launch = _launch(_runtime(tmp_path), _spec(_git_values()))

        assert launch["repo"] == REPO
        assert launch["git"] == {
            "repo_url": REPO,
            "token_file": TOKEN_FILE,
            "token_mount": "/run/secrets",
            "token_path": "git/abc123/token",
            "username": "x-access-token",
            "user_name": "ada@example.test",
            "user_email": "ada@example.test",
            "base_branch": "main",
        }
        holder = next(m for m in launch["volume_mounts"] if m["target"] == "/run/secrets")
        assert holder["source"].endswith("/volumes/openbao-secrets")

    def test_the_session_git_reads_the_token_file_like_a_kubernetes_broker(self, tmp_path):
        environment = _launch(_runtime(tmp_path), _spec(_git_values()))["environment"]

        assert environment["GIT_TOKEN_FILE"] == TOKEN_FILE
        assert environment["GIT_USERNAME"] == "x-access-token"
        assert environment["GIT_CONFIG_COUNT"] == "1"
        assert environment["GIT_CONFIG_KEY_0"] == f"credential.{REPO}.helper"
        helper = environment["GIT_CONFIG_VALUE_0"]
        token = tmp_path / "token"
        token.write_text("the-token")
        answered = subprocess.run(
            ["sh", "-c", helper.removeprefix("!") + " get"],
            env={"PATH": "/usr/bin:/bin", "GIT_TOKEN_FILE": str(token), "GIT_USERNAME": "x"},
            capture_output=True,
            text=True,
            check=True,
        )
        assert answered.stdout == "username=x\npassword=the-token\n"

    def test_a_token_on_no_mount_of_the_session_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="on no mount of the VM session"):
            _launch(_runtime(tmp_path), _spec(_git_values("/etc/git/token")))

    def test_without_an_integration_token_the_launch_is_as_before(self, tmp_path):
        launch = _launch(_runtime(tmp_path), _spec(None))

        assert launch["repo"] == "https://github.com/acme/private"
        assert "git" not in launch
        assert "GIT_TOKEN_FILE" not in launch["environment"]

    def test_a_runtime_that_clones_with_its_own_program_gets_neither(self, tmp_path):
        runtime = _runtime(tmp_path)
        runtime._clones_with_integration_token = HostModeRuntime._clones_with_integration_token

        launch = _launch(runtime, _spec(_git_values()))

        assert "git" not in launch
        assert "GIT_TOKEN_FILE" not in launch["environment"]


def _guest(tmp_path: Path) -> tuple[Path, Path]:
    """Fake ``docker``, ``git`` and ``chown`` that record their calls; a clone asks its helper."""
    binary = tmp_path / "bin"
    binary.mkdir()
    calls = tmp_path / "calls"
    docker = binary / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "docker %s\\n" "$*" >> "$CALLS"\n'
        '[ "$1 $2" = "container inspect" ] && exit 1\nexit 0\n'
    )
    git = binary / "git"
    # The clone runs the helper it was given, as Git does when the server asks for a login.
    git.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['CALLS'], 'a') as calls:\n"
        "    calls.write(json.dumps(args) + '\\n')\n"
        "if 'clone' in args:\n"
        "    helper = next(a.split('=', 1)[1] for a in args if '.helper=!' in a)\n"
        "    answer = subprocess.run(['sh', '-c', helper[1:] + ' get'], capture_output=True,\n"
        "        text=True, check=True).stdout\n"
        "    with open(os.environ['ANSWER'], 'w') as out:\n"
        "        out.write(answer)\n"
        "    os.makedirs(os.path.join(args[-1], '.git'))\n"
    )
    chown = binary / "chown"
    chown.write_text('#!/bin/sh\nprintf "chown %s\\n" "$*" >> "$CALLS"\n')
    for tool in (docker, git, chown):
        tool.chmod(0o755)
    return binary, calls


def _payload(tmp_path: Path, secrets: Path, git: dict | None) -> dict:
    root = tmp_path / "session"
    root.mkdir()
    (root / ".allocation").write_text("slot-marker")
    uid = os.getuid()
    payload = {
        "allocation_id": str(uuid4()),
        "image": "test/skuld:dev",
        "environment": {"SESSION_ID": str(uuid4())},
        "repo": REPO,
        "branch": "main",
        "volume_mounts": [{"source": str(secrets), "target": "/run/secrets", "readonly": True}],
        "slot": {
            "data_root": str(root),
            "skuld": "niuu-skuld-0",
            "labels": ["compute.niuu.io/slot=0"],
            "uid": uid,
            "identity": str(tmp_path / "identity"),
            "holder": "holder-0",
            "slice": "niuu-slot-0.slice",
            "marker": "slot-marker",
            "sidecar_prefix": "niuu-0-",
            "netns": "niuu-0",
        },
    }
    if git is not None:
        payload["git"] = git
    return payload


def _start(tmp_path: Path, payload: dict, binary: Path, calls: Path):
    return subprocess.run(
        [sys.executable, "-c", module._START],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env={
            "CALLS": str(calls),
            "ANSWER": str(tmp_path / "answer"),
            "PATH": f"{binary}:/usr/bin:/bin",
        },
    )


def _payload_git() -> dict:
    return {
        "repo_url": REPO,
        "token_file": TOKEN_FILE,
        "token_mount": "/run/secrets",
        "token_path": "git/abc123/token",
        "username": "x-access-token",
        "user_name": "Ada",
        "user_email": "ada@example.test",
        "base_branch": "main",
    }


class TestGuestClone:
    def test_the_clone_logs_in_with_the_rendered_token_and_keeps_only_its_path(self, tmp_path):
        binary, calls = _guest(tmp_path)
        secrets = tmp_path / "secrets"
        (secrets / "git" / "abc123").mkdir(parents=True)
        (secrets / "git" / "abc123" / "token").write_text("the-secret-token")

        result = _start(tmp_path, _payload(tmp_path, secrets, _payload_git()), binary, calls)

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "answer").read_text() == (
            "username=x-access-token\npassword=the-secret-token\n"
        )
        recorded = calls.read_text()
        assert "the-secret-token" not in recorded
        git_calls = [json.loads(line) for line in recorded.splitlines() if line.startswith("[")]
        clone = git_calls[0]
        assert clone[:6] == [
            "-c",
            "credential.helper=",
            "-c",
            "credential.useHttpPath=true",
            "-c",
            clone[5],
        ]
        assert clone[5].startswith(f"credential.{REPO}.helper=!")
        assert clone[6:] == [
            "clone",
            "--branch",
            "main",
            "--",
            REPO,
            str(tmp_path / "session" / "workspace"),
        ]
        settings = {call[3]: call[4] for call in git_calls[1:]}
        # Inside the session's containers the token is at its own path, read on every use.
        assert settings["niuu.gitTokenFile"] == TOKEN_FILE
        assert settings["niuu.gitUsername"] == "x-access-token"
        assert settings["credential.helper"] == ""
        assert settings["credential.useHttpPath"] == "true"
        assert "git config niuu.gitTokenFile" in settings[f"credential.{REPO}.helper"]
        assert settings["user.name"] == "Ada"
        assert settings["user.email"] == "ada@example.test"
        assert settings["niuu.baseBranch"] == "main"
        assert "docker run --detach" in recorded

    def test_a_token_that_was_not_rendered_names_the_clone_and_nothing_else(self, tmp_path):
        binary, calls = _guest(tmp_path)
        secrets = tmp_path / "secrets"
        secrets.mkdir()

        result = _start(tmp_path, _payload(tmp_path, secrets, _payload_git()), binary, calls)

        assert result.returncode != 0
        with pytest.raises(VmRuntimeStageError) as raised:
            SshGuestAccess._raise_command_error(
                result.returncode,
                result.stderr.encode(),
                safe_error_prefix=vm_slot_supervisor.STAGE_PREFIX,
                safe_detail_prefix=vm_slot_supervisor.DETAIL_PREFIX,
            )
        assert str(raised.value) == (
            "Guest runtime startup failed at stage clone: the Git integration token was not "
            "rendered"
        )
        assert not any(line.startswith("[") for line in calls.read_text().splitlines())

    def test_a_public_clone_without_an_integration_runs_plain_git(self, tmp_path):
        binary, calls = _guest(tmp_path)
        git = binary / "git"
        git.write_text(
            "#!/bin/sh\n"
            'python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "$@" >> "$CALLS"\n'
            'mkdir -p "$(eval echo \\${$#})/.git"\n'
        )

        result = _start(tmp_path, _payload(tmp_path, tmp_path, None), binary, calls)

        assert result.returncode == 0, result.stderr
        git_calls = [json.loads(line) for line in calls.read_text().splitlines() if line[0] == "["]
        assert git_calls == [
            ["clone", "--branch", "main", "--", REPO, str(tmp_path / "session" / "workspace")]
        ]
