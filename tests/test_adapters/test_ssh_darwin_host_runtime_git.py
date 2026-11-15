"""A Mac host session clones a private repository with its attached Git integration.

Without mount namespaces every container path of a Mac session is placed where its
mount's source lives. ``_place_launch`` does that for the Git section too: where root
reads the token the slot's agent rendered, and where the session's Git reads it, the
same place its ``GIT_TOKEN_FILE`` names. ``START`` reads the token and clones with it
through ``prepare_storage``, shared with Linux.
"""

import base64
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from tests.test_adapters.test_ssh_darwin_host_runtime import PORTS, _runtime
from tests.test_adapters.test_ssh_vm_runtime_git import REPO, TOKEN_FILE
from volundr.adapters.outbound import darwin_guest_programs as programs
from volundr.adapters.outbound.ssh_darwin_host_runtime import DarwinSlotLayout
from volundr.adapters.outbound.ssh_guest_access import SshGuestAccess
from volundr.adapters.outbound.vm_slot_supervisor import (
    DETAIL_PREFIX,
    DIAGNOSTIC_LIMIT,
    STAGE_PREFIX,
)
from volundr.domain.vm_runtime import VmRuntimeStageError

TOKEN = "ghs_s3cr3t"
GIT = {
    "repo_url": REPO,
    "token_file": TOKEN_FILE,
    "token_mount": "/run/secrets",
    "token_path": "git/abc123/token",
    "username": "x-access-token",
    "user_name": "Ada",
    "user_email": "",
    "base_branch": "",
}


def test_the_token_is_placed_where_the_session_environment_reads_it(tmp_path):
    runtime = _runtime(tmp_path)
    layout = DarwinSlotLayout(allocation_id=uuid4(), slot=0, uid_base=61000, **PORTS)
    payload = {
        "environment": {"GIT_TOKEN_FILE": TOKEN_FILE},
        "repo": REPO,
        "git": dict(GIT),
        "secret_mounts": [],
        "volume_mounts": [{"source": "/run/niuu/session/secrets", "target": "/run/secrets"}],
        "directories": [],
    }

    placed = runtime._place_launch(layout, payload)

    secrets = layout.relocate("/run/niuu/session/secrets")
    assert placed["git"]["token_source"] == f"{secrets}/git/abc123/token"
    # The repository's config and the session's environment name the same file on the Mac.
    assert placed["git"]["token_file"] == placed["environment"]["GIT_TOKEN_FILE"]
    assert placed["git"]["token_file"] != TOKEN_FILE
    assert payload["git"]["token_file"] == TOKEN_FILE


def test_a_token_on_no_mount_of_the_mac_session_is_refused(tmp_path):
    runtime = _runtime(tmp_path)
    layout = DarwinSlotLayout(allocation_id=uuid4(), slot=0, uid_base=61000, **PORTS)
    payload = {
        "environment": {},
        "git": dict(GIT),
        "secret_mounts": [],
        "volume_mounts": [],
        "directories": [],
    }

    with pytest.raises(ValueError, match="on no mount of the Mac session"):
        runtime._place_launch(layout, payload)


def _start(tmp_path: Path, *, rendered: bool) -> subprocess.CompletedProcess:
    """``START`` up to its workspace step, off a Mac; ``prepare_storage`` prints its ``git``."""
    token = tmp_path / "secrets" / "git" / "abc123" / "token"
    if rendered:
        token.parent.mkdir(parents=True)
        token.write_text(TOKEN + "\n")
    program = (
        programs.START.replace("if loaded(label):", "if False:")
        .replace("workspace, home = prepare_storage(", "workspace, home = replaced_storage(")
        .replace(
            "stage = ['payload']",
            "def replaced_storage(*args):\n"
            "    print(json.dumps(args[6]))\n"
            "    sys.exit(0)\n"
            "stage = ['payload']",
        )
    )
    payload = {
        "slot": {"uid": 61000, "data_root": str(tmp_path), "marker": "m"},
        "darwin": {
            "label": "world.niuu.slot.0",
            "nix": False,
            "links": [],
            "state_root": str(tmp_path / "state"),
            "log_root": str(tmp_path / "logs"),
            "native_path": "/usr/bin:/bin",
            "agent_home": str(tmp_path / "agent-home"),
            "agent_tmp": str(tmp_path / "agent-tmp"),
        },
        "repo": REPO,
        "branch": "main",
        "git": {**GIT, "token_source": str(token), "token_file": "/mac/secrets/token"},
    }
    return subprocess.run(
        [sys.executable, "-I", "-c", program],
        input=json.dumps(payload).encode(),
        capture_output=True,
    )


def test_start_reads_the_rendered_token_and_hands_it_to_the_clone(tmp_path):
    completed = _start(tmp_path, rendered=True)

    assert completed.returncode == 0, completed.stderr
    git = json.loads(completed.stdout)
    assert git["token"] == TOKEN
    assert dict(git["settings"])["niuu.gitTokenFile"] == "/mac/secrets/token"
    assert TOKEN not in json.dumps(git["settings"])


def _stage_error(completed: subprocess.CompletedProcess) -> VmRuntimeStageError:
    with pytest.raises(VmRuntimeStageError) as raised:
        SshGuestAccess._raise_command_error(
            completed.returncode,
            completed.stderr,
            safe_error_prefix=STAGE_PREFIX,
            safe_detail_prefix=DETAIL_PREFIX,
        )
    return raised.value


def test_a_token_that_was_not_rendered_fails_the_mac_workspace_step(tmp_path):
    error = _stage_error(_start(tmp_path, rendered=False))

    assert error.stage == "host-workspace"
    assert error.diagnostic == "The Git integration token was not rendered at /mac/secrets/token"


def test_a_long_detail_is_one_printable_line_that_says_it_was_cut():
    program = programs._SLOT_COMMON + "\nfail('slot-up', 'line\\tone\\n' + 'x' * 2000)\n"
    completed = subprocess.run([sys.executable, "-I", "-c", program], capture_output=True)

    error = _stage_error(completed)
    assert error.stage == "slot-up"
    assert error.diagnostic.startswith("line one xxx")
    assert error.diagnostic.endswith(" [truncated]")
    assert len(error.diagnostic) == DIAGNOSTIC_LIMIT
    encoded = completed.stderr.decode().split(DETAIL_PREFIX)[1].strip()
    assert base64.b64decode(encoded).decode() == error.diagnostic


def test_the_mac_clones_after_its_agents_rendered_the_secrets():
    # The init containers render the integration's token; the clone reads it.
    start = programs.START
    assert start.index("for c in p.get('init_containers'") < start.index("stage[0] = 'workspace'")
    assert start.index("stage[0] = 'workspace'") < start.index("stage[0] = 'launch'")
