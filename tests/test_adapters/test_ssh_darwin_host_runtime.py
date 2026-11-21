"""SshDarwinHostRuntime: sessions directly on a Mac, as a fresh macOS user.

The controller side runs against a recording guest access. The bundle install runs for
real against a fake registry (a ``curl`` on PATH), and the boot script against a fake
Skuld; the programs that need root and macOS (users, launchd) are proven on a Mac.
"""

import ast
import base64
import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_adapters.test_ssh_vm_runtime_slots import SlotView
from volundr.adapters.outbound import darwin_guest_programs as programs
from volundr.adapters.outbound.host_checks import DARWIN_AUDIT_SH
from volundr.adapters.outbound.host_session_programs import SESSION_BOOT
from volundr.adapters.outbound.ssh_darwin_host_runtime import (
    BUNDLE_CACHE,
    BUNDLE_MEDIA_TYPE,
    DarwinSlotLayout,
    SshDarwinHostRuntime,
    _group_mode,
    translate,
)
from volundr.adapters.outbound.ssh_guest_access import SshGuestAccess
from volundr.adapters.outbound.ssh_vm_runtime import SlotNotCleanError
from volundr.adapters.outbound.vm_slot_supervisor import DETAIL_PREFIX, STAGE_PREFIX
from volundr.domain.compute import Machine, MachineBootstrap, MachineState
from volundr.domain.compute_hosts import HostPlatform
from volundr.domain.guest_access import GuestCommandResult
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec
from volundr.domain.ports import HostedUnitError
from volundr.domain.services.host_access_key import generate_host_access_key
from volundr.domain.session_runtimes import SessionRuntimeKind
from volundr.domain.vm_runtime import SessionImages, VmRuntimeStageError

DIGEST = "b" * 64
BUNDLE = f"ghcr.io/niuulabs/skuld-darwin@sha256:{DIGEST}"
BAO = "quay.io/openbao/openbao@sha256:" + "c" * 64
ROOT = f"/var/lib/niuu/host-bundles/{DIGEST}"
DESCRIPTION = {"format": 1, "platform": "darwin-arm64"}


def _runtime(tmp_path: Path, **kwargs) -> SshDarwinHostRuntime:
    options = {
        "host_key": generate_host_access_key(),
        "data_dir": str(tmp_path / "data"),
        "skuld_bundle": BUNDLE,
        "session_images": SessionImages(native=(BAO,)),
        "command_timeout_seconds": 0.05,
    }
    return SshDarwinHostRuntime(**{**options, **kwargs})


def _session() -> Session:
    return Session(name="test", owner_id="owner", tenant_id="tenant", source=GitSource())


SPEC = SessionSpec(values={"session": {"systemPrompt": "Be brief."}}, pod_spec=PodSpecAdditions())

#: The OpenBao agent as the secret injection describes it for VM sessions.
OPENBAO_POD = PodSpecAdditions(
    volumes=(
        {"name": "openbao-secrets", "emptyDir": {}},
        {"name": "openbao-agent-home", "emptyDir": {}},
    ),
    volume_mounts=({"name": "openbao-secrets", "mountPath": "/run/secrets", "readOnly": True},),
    init_containers=(
        {
            "name": "openbao-agent-init",
            "image": BAO,
            "command": [
                "/bin/sh",
                "-ec",
                'printf "%s" "$CONFIG" > /home/vault/config.hcl && exec bao agent '
                "-config=/home/vault/config.hcl",
            ],
            "env": [
                {
                    "name": "CONFIG",
                    "value": 'template { destination = "/run/secrets/user/claude.json" }\n'
                    'sink { path = "/home/vault/.vault-token" }',
                }
            ],
            "volumeMounts": [
                {"name": "openbao-secrets", "mountPath": "/run/secrets"},
                {"name": "openbao-agent-home", "mountPath": "/home/vault"},
            ],
            "securityContext": {"runAsUser": 100, "runAsGroup": 1000},
        },
    ),
    extra_containers=(
        {
            "name": "openbao-agent",
            "image": BAO,
            "command": ["bao", "agent", "-config=/home/vault/config.hcl"],
            "volumeMounts": [
                {"name": "openbao-secrets", "mountPath": "/run/secrets"},
                {"name": "openbao-agent-home", "mountPath": "/home/vault"},
            ],
        },
    ),
)


def _launch(bootstrap: MachineBootstrap) -> dict:
    return json.loads(
        next(f.content for f in bootstrap.files if f.path == "/etc/niuu/session-launch.json")
    )


def _machine(allocation) -> Machine:
    return Machine(
        allocation_id=allocation,
        resource_id="mac",
        state=MachineState.RUNNING,
        addresses=("192.0.2.38",),
    )


@pytest.fixture
def mac(tmp_path):
    runtime = _runtime(tmp_path)
    allocation = uuid4()
    session = _session()
    spec = SessionSpec(values=SPEC.values, pod_spec=OPENBAO_POD)
    bootstrap = runtime.bootstrap(session, spec, MachineBootstrap())
    lease = SlotView(allocation, session.id, 0, _machine(allocation))

    runtime._guest_access.execute = AsyncMock(
        return_value=GuestCommandResult(0, json.dumps(DESCRIPTION).encode())
    )
    runtime._guest_access.open_tunnel = AsyncMock()
    runtime._guest_access.close_tunnel = AsyncMock()
    return runtime, bootstrap, lease


def _calls(runtime):
    return runtime._guest_access.execute.await_args_list


def _program(call) -> str:
    return call.args[2]


# -- configuration -----------------------------------------------------------------------------


def test_a_mac_runtime_runs_sessions_on_darwin_hosts_only(tmp_path):
    runtime = _runtime(tmp_path)

    assert runtime.host_platforms == frozenset({HostPlatform.DARWIN})
    assert runtime.session_runtime is SessionRuntimeKind.HOST
    assert runtime.requires_root_daemon is False
    assert runtime.supports_slots is False
    assert runtime.supports_reuse is True
    limits = runtime.slot_limits
    assert (limits.uid_base, limits.cpu, limits.memory_mib, limits.pids) == (
        61000,
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"slot_isolation": {"cpu": 1, "memory_mib": 1, "pids": 1}}, "remove slot_isolation"),
        ({"skuld_image": "ghcr.io/niuulabs/skuld:dev"}, "set skuld_bundle"),
        ({"skuld_bundle": "ghcr.io/niuulabs/skuld-darwin:dev"}, "digest-pinned"),
        ({"uid_base": 500}, "uid_base"),
        ({"session_start_timeout_seconds": 0}, "session_start_timeout_seconds"),
        (
            {"session_images": SessionImages(native=("quay.io/openbao/openbao:2.5.3",))},
            "compute.session_images.native",
        ),
    ],
)
def test_configuration_a_mac_cannot_honour_is_refused(tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        _runtime(tmp_path, **kwargs)


def test_the_release_bundle_stands_in_for_skuld_bundle(tmp_path):
    runtime = _runtime(tmp_path, skuld_bundle=None, session_images=SessionImages(darwin=BUNDLE))

    assert runtime._python("print(1)").startswith(f"sudo -n {ROOT}/python/bin/python3 -I -c ")


def test_the_release_bundle_and_a_configured_one_are_not_both_accepted(tmp_path):
    with pytest.raises(ValueError, match="remove skuld_bundle to run the release's"):
        _runtime(tmp_path, session_images=SessionImages(darwin=BUNDLE))


def test_a_mac_runtime_needs_a_bundle_from_one_place_or_the_other(tmp_path):
    for images in (None, SessionImages(linux="ghcr.io/niuulabs/openshell@sha256:" + "a" * 64)):
        with pytest.raises(ValueError, match="compute.session_images.darwin"):
            _runtime(tmp_path, skuld_bundle=None, session_images=images)


def test_a_release_bundle_must_still_be_digest_pinned(tmp_path):
    images = SessionImages(darwin="ghcr.io/niuulabs/skuld-darwin:dev")

    with pytest.raises(ValueError, match="digest-pinned"):
        _runtime(tmp_path, skuld_bundle=None, session_images=images)


async def test_recycling_a_mac_slot_audits_slot_zero_alone(mac):
    runtime, bootstrap, lease = mac
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(0, b""))

    await runtime.recycle(lease, bootstrap)

    [call] = runtime._guest_access.execute.await_args_list
    assert call.args[2] == "sudo -n sh -s -- 61000 1 '' 0 check 0"
    assert call.kwargs["data"] == DARWIN_AUDIT_SH.encode()


async def test_a_mac_slot_with_anything_left_is_not_recycled(mac):
    runtime, bootstrap, lease = mac
    runtime._guest_access.execute = AsyncMock(
        return_value=GuestCommandResult(0, b"finding user niuu-slot-0 still exists\n")
    )

    with pytest.raises(SlotNotCleanError, match="user niuu-slot-0 still exists"):
        await runtime.recycle(lease, bootstrap)


def test_guest_programs_run_under_the_bundles_isolated_python_as_root(tmp_path):
    command = _runtime(tmp_path)._python("print(1)", "arg")

    assert command.startswith(f"sudo -n {ROOT}/python/bin/python3 -I -c ")
    assert command.endswith(" arg")


def test_every_guest_program_parses_and_install_runs_on_apples_python():
    for name in ("INSTALL", "PRUNE", "SLOT", "START", "INIT", "STOP"):
        ast.parse(getattr(programs, name))
    # INSTALL runs before the bundle exists, under the Command Line Tools' Python 3.9.
    ast.parse(programs.INSTALL, feature_version=(3, 9))


PORTS = {"broker_port": 8081, "platform_port": 18080}


def test_a_mac_has_only_slot_zero(tmp_path):
    with pytest.raises(ValueError, match="one session at a time.*darwin.sharedSlots"):
        DarwinSlotLayout(allocation_id=uuid4(), slot=1, uid_base=61000, **PORTS)


def test_a_mac_with_shared_slots_has_its_own_ports_and_metadata_address_per_slot():
    zero, two = (
        DarwinSlotLayout(allocation_id=uuid4(), slot=n, uid_base=61000, shared=True, **PORTS)
        for n in (0, 2)
    )

    assert (zero.skuld_port, zero.callback_port, zero.metadata_address) == (
        8081,
        18080,
        "169.254.169.254",
    )
    assert (two.name, two.uid, two.label) == ("niuu-slot-2", 61002, "world.niuu.slot.2")
    assert (two.skuld_port, two.callback_port, two.metadata_address) == (
        8083,
        18082,
        "169.254.169.252",
    )
    assert two.data_root == "/var/lib/niuu/slots/2"


def test_slots_whose_ports_would_overlap_are_refused():
    with pytest.raises(ValueError, match="ports overlap"):
        DarwinSlotLayout(
            allocation_id=uuid4(),
            slot=3,
            uid_base=61000,
            shared=True,
            broker_port=8081,
            platform_port=8083,
        )
    with pytest.raises(ValueError, match="at most 254 slots"):
        DarwinSlotLayout(allocation_id=uuid4(), slot=254, uid_base=61000, shared=True, **PORTS)


def test_the_slot_layout_is_where_the_darwin_audit_looks():
    layout = DarwinSlotLayout(allocation_id=uuid4(), slot=0, uid_base=61000, **PORTS)

    assert (layout.name, layout.uid, layout.label) == ("niuu-slot-0", 61000, "world.niuu.slot.0")
    assert layout.key == str(layout.allocation_id)
    assert layout.data_root == "/var/lib/niuu/slots/0"
    assert layout.run_root == "/var/run/niuu/slots/0"
    assert (
        layout.relocate("/etc/niuu/session-secrets/0") == "/var/run/niuu/slots/0/session-secrets/0"
    )
    assert layout.relocate("/etc/niuu/guest-agent") == "/var/run/niuu/host-sessions/0/guest-agent"
    assert layout.relocate("/run/niuu/session/volumes/x") == "/var/run/niuu/slots/0/run/volumes/x"
    assert layout.relocate("/etc/ssh/ssh_host_ed25519_key") == "/etc/ssh/ssh_host_ed25519_key"
    spec = layout.spec(stop_timeout=30, poll=2)
    assert spec["audit"] == DARWIN_AUDIT_SH
    assert spec["label"] == "world.niuu.slot.0"


def _remove_owned(tmp_path, stderr: str, exit_code: int) -> subprocess.CompletedProcess:
    """``remove_owned`` of the slot programs, against a ``find`` that answers as given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "find"
    fake.write_text(
        f'#!/bin/sh\nprintf "%s " "$@" > {shlex.quote(str(tmp_path / "args"))}\n'
        f"printf '%s' {shlex.quote(stderr)} >&2\nexit {exit_code}\n"
    )
    fake.chmod(0o755)
    program = programs._SLOT_COMMON + "\nremove_owned('/private/var/folders', 61000)\n"
    return subprocess.run(
        [sys.executable, "-I", "-c", program],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )


def test_slot_down_keeps_the_folder_skeleton_macos_will_not_let_root_remove(tmp_path):
    result = _remove_owned(tmp_path, "", 0)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "args").read_text().split() == [
        "/private/var/folders",
        "-xdev",
        "-uid",
        "61000",
        "!",
        "-flags",
        "+sunlnk",
        "-prune",
        "-exec",
        "rm",
        "-rf",
        "{}",
        "+",
    ]


def test_slot_down_passes_over_directories_root_may_not_read(tmp_path):
    result = _remove_owned(
        tmp_path,
        "find: /private/var/folders/zz/x/C/com.apple.a: Operation not permitted\n"
        "find: /private/var/folders/zz/x/C/com.apple.b: Permission denied\n",
        1,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("stderr", "exit_code", "detail"),
    [
        ("rm: /private/var/folders/x: Directory not empty\n", 1, "Directory not empty"),
        ("", 1, "find exited 1"),
        (
            "find: /a: Operation not permitted\nfind: /b: No such file or directory\n",
            1,
            "No such file or directory",
        ),
    ],
)
def test_slot_down_fails_on_any_other_find_error(tmp_path, stderr, exit_code, detail):
    result = _remove_owned(tmp_path, stderr, exit_code)

    assert result.returncode == 1
    # What Forge shows: the stage, and the detail decoded as SshGuestAccess reads it.
    with pytest.raises(VmRuntimeStageError) as raised:
        SshGuestAccess._raise_command_error(
            result.returncode,
            result.stderr.encode(),
            safe_error_prefix=STAGE_PREFIX,
            safe_detail_prefix=DETAIL_PREFIX,
        )
    assert raised.value.stage == "slot-down"
    assert detail in raised.value.diagnostic
    assert "Operation not permitted" not in raised.value.diagnostic


# -- translation -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/workspace", "/mac/ws"),
        ("/workspace/repo", "/mac/ws/repo"),
        ("cd /workspace && ls", "cd /mac/ws && ls"),
        ('path = "/run/secrets/user/x"', 'path = "/mac/secrets/user/x"'),
        ("-config=/home/vault/config.hcl", "-config=/mac/vault/config.hcl"),
        ("/a:/workspace/bin:/b", "/a:/mac/ws/bin:/b"),
        ("https://x/workspace/y", "https://x/workspace/y"),
        ("/workspaces", "/workspaces"),
        ("/workspace.bak", "/workspace.bak"),
        ("/var/run/secrets", "/var/run/secrets"),
        ("/run/secrets-old", "/run/secrets-old"),
    ],
)
def test_translation_moves_whole_paths_only(text, expected):
    places = {"/workspace": "/mac/ws", "/run/secrets": "/mac/secrets", "/home/vault": "/mac/vault"}

    assert translate(text, places) == expected


def test_translation_is_one_pass_longest_first():
    places = {"/a": "/a/b", "/a/b": "/z"}

    assert translate("/a/b/c /a/x", places) == "/z/c /a/b/x"
    assert translate("/a", {}) == "/a"


@pytest.mark.parametrize(
    ("mode", "expected"), [("0777", "0770"), ("0755", "0750"), ("0700", "0700")]
)
def test_world_access_becomes_the_slot_groups(mode, expected):
    assert _group_mode(mode) == expected


# -- the session's bootstrap -------------------------------------------------------------------


def test_the_session_runs_the_bundles_agents_first_then_nix_then_the_mac(tmp_path):
    runtime = _runtime(tmp_path)

    launch = _launch(runtime.bootstrap(_session(), SPEC, MachineBootstrap()))

    environment = launch["environment"]
    assert environment["PATH"].split(":") == [
        f"{ROOT}/bin",
        "/home/skuld/.nix-profile/bin",
        "/nix/var/nix/profiles/default/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
        f"{ROOT}/fallback",
    ]
    assert environment["NIUU_SKULD_PYTHON"] == f"{ROOT}/libexec/python"
    assert environment["NIX_REMOTE"] == "daemon"
    prompt = environment["SKULD__SESSION__SYSTEM_PROMPT"]
    assert prompt.startswith("Be brief.\n\n")
    assert "directly on a Mac (macOS) as the unprivileged user {slot_user}" in prompt
    assert "nix profile install" in prompt
    assert environment["SKULD__PREVIEW__ENABLED"] == "false"


def test_without_nix_the_agent_is_only_told_where_it_runs(tmp_path):
    runtime = _runtime(tmp_path, nix=False)
    spec = SessionSpec(values={"session": {}}, pod_spec=PodSpecAdditions())

    environment = _launch(runtime.bootstrap(_session(), spec, MachineBootstrap()))["environment"]

    assert "nix" not in environment["PATH"]
    assert "NIX_REMOTE" not in environment
    assert environment["SKULD__SESSION__SYSTEM_PROMPT"].startswith("You are running directly")


def test_live_preview_finds_the_sessions_listeners_with_lsof(tmp_path):
    runtime = _runtime(
        tmp_path, preview_router_auth={"adapter": "skuld.preview_adapters.X", "kwargs": {}}
    )
    session = _session()

    environment = _launch(runtime.bootstrap(session, SPEC, MachineBootstrap()))["environment"]

    scanner = json.loads(environment["SKULD__PREVIEW__PORT_SCANNER"])
    assert scanner["adapter"] == "skuld.preview_adapters.LsofSessionPortScanner"
    assert scanner["kwargs"]["session_id"] == str(session.id)


def test_a_container_the_mac_cannot_run_is_refused_at_create(tmp_path):
    runtime = _runtime(tmp_path, session_images=None)
    spec = SessionSpec(values=SPEC.values, pod_spec=OPENBAO_POD)

    with pytest.raises(ValueError, match="compute.session_images.native"):
        runtime.bootstrap(_session(), spec, MachineBootstrap())


# -- start and stop ----------------------------------------------------------------------------


async def test_start_fetches_the_bundle_makes_the_slot_then_starts_the_session(mac):
    runtime, bootstrap, lease = mac
    steps = []
    runtime.report_startup_to(lambda sid, key, label: steps.append(key))

    await runtime.start(lease, bootstrap)

    calls = _calls(runtime)
    commands = [_program(call) for call in calls]
    assert commands[0].startswith("sudo -n python3 -c ")
    assert commands[0].endswith(f"{BUNDLE} /var/lib/niuu/host-bundles {BUNDLE_MEDIA_TYPE}")
    assert commands[1] == runtime._python(
        programs.PRUNE, "/var/lib/niuu/host-bundles", DIGEST, "world.niuu.slot"
    )
    assert commands[2] == runtime._python(programs.SLOT, "up")
    slot = json.loads(calls[2].kwargs["data"])
    assert (slot["name"], slot["uid"]) == ("niuu-slot-0", 61000)
    assert slot["label"] == "world.niuu.slot.0"
    assert calls[2].kwargs["safe_error_prefix"] == "NIUU_SLOT_STAGE:"
    assert commands[-1] == runtime._python(programs.START)
    # A failed start names its step and reason, as the slot program does.
    assert calls[-1].kwargs["safe_error_prefix"] == STAGE_PREFIX
    assert calls[-1].kwargs["safe_detail_prefix"] == DETAIL_PREFIX
    placed = json.loads(calls[-1].kwargs["data"])
    environment = placed["environment"]
    assert environment["HOME"] == "/var/lib/niuu/slots/0/session/home"
    assert environment["WORKSPACE_DIR"] == "/var/lib/niuu/slots/0/session/workspace"
    assert environment["TMPDIR"] == "/var/lib/niuu/slots/0/tmp"
    # Skuld's home is the slot's, not the cluster layout under /volundr/sessions.
    assert environment["SKULD__HOME_DIR"] == environment["HOME"]
    assert (
        environment["PATH"].split(":")[1] == "/var/lib/niuu/slots/0/session/home/.nix-profile/bin"
    )
    assert environment["NIUU_SECRETS_DIR"] == "/var/run/niuu/slots/0/run/volumes/openbao-secrets"
    assert json.loads(environment["NIUU_SESSION_PLACES"]) == {
        "/home/skuld": "/var/lib/niuu/slots/0/session/home",
        "/workspace": "/var/lib/niuu/slots/0/session/workspace",
    }
    assert json.loads(environment["NIUU_SESSION_LINKS"]) == []
    assert "unprivileged user niuu-slot-0" in environment["SKULD__SESSION__SYSTEM_PROMPT"]
    assert environment["SKULD__HOST"] == "127.0.0.1"
    init = placed["init_containers"][0]
    secrets = "/var/run/niuu/slots/0/run/volumes/openbao-secrets"
    vault = "/var/run/niuu/slots/0/run/volumes/openbao-agent-home"
    assert init["command"][2] == (
        f'printf "%s" "$CONFIG" > {vault}/config.hcl && exec bao agent -config={vault}/config.hcl'
    )
    assert init["environment"]["CONFIG"] == (
        f'template {{ destination = "{secrets}/user/claude.json" }}\n'
        f'sink {{ path = "{vault}/.vault-token" }}'
    )
    assert placed["sidecars"][0]["command"] == ["bao", "agent", f"-config={vault}/config.hcl"]
    modes = {item["path"]: item["mode"] for item in placed["directories"]}
    # The session reads its secrets through its group; the agent's home is root's alone.
    assert modes[secrets] == "0770"
    assert modes[vault] == "0700"
    darwin = placed["darwin"]
    assert darwin["label"] == "world.niuu.slot.0"
    assert darwin["python"] == f"{ROOT}/python/bin/python3"
    assert darwin["native_path"].startswith(f"{ROOT}/libexec:{ROOT}/bin:")
    assert darwin["init"] == programs.INIT and darwin["boot"] == SESSION_BOOT
    assert darwin["links"] == []
    assert placed["slot"] == {
        "index": 0,
        "uid": 61000,
        "data_root": "/var/lib/niuu/slots/0/session",
        "marker": str(lease.id),
    }
    runtime._guest_access.open_tunnel.assert_awaited_with(
        lease,
        bootstrap,
        remote_port=8081,
        reverse_bind_host="127.0.0.1",
        reverse_port=18080,
        controller_host="127.0.0.1",
        controller_port=8080,
        key=str(lease.id),
    )
    assert steps == ["guest_bundle", "guest_slot", "guest_files", "guest_tunnel", "guest_host"]


async def test_mounts_at_fixed_paths_become_links_of_the_session_or_of_root(tmp_path):
    runtime = _runtime(tmp_path)
    layout = DarwinSlotLayout(allocation_id=uuid4(), slot=0, uid_base=61000, **PORTS)
    payload = {
        "environment": {"TOKEN": "/var/run/secrets/niuu-workload/token"},
        "secret_mounts": [
            {"source": "/etc/niuu/session-secrets/0", "target": "/home/skuld/.config/gh/hosts.yml"}
        ],
        "volume_mounts": [
            {
                "source": "/run/niuu/session/tokens/workload",
                "target": "/var/run/secrets/niuu-workload",
                "readonly": True,
            }
        ],
        "directories": [{"path": "/run/niuu/session/tokens/workload", "mode": "0755"}],
    }

    placed = runtime._place_launch(layout, payload)

    tokens = "/var/run/niuu/slots/0/run/tokens/workload"
    assert placed["environment"]["TOKEN"] == f"{tokens}/token"
    assert json.loads(placed["environment"]["NIUU_SESSION_LINKS"]) == [
        {
            "target": "/var/lib/niuu/slots/0/session/home/.config/gh/hosts.yml",
            "source": "/var/run/niuu/slots/0/session-secrets/0",
        }
    ]
    assert placed["darwin"]["links"] == [
        {"target": "/var/run/secrets/niuu-workload", "source": tokens}
    ]
    assert placed["directories"] == [{"path": tokens, "mode": "0750"}]
    assert "NIUU_SECRETS_DIR" not in placed["environment"]
    spec = runtime._slot_spec(layout, payload)
    assert spec["links"] == ["/var/run/secrets/niuu-workload"]


async def test_the_guest_agents_configuration_names_the_macs_places(mac):
    runtime, bootstrap, lease = mac
    config = {
        "listen": {"address": "169.254.169.254", "port": 80},
        "token_volumes": [{"directory": "/run/niuu/session/tokens/sa"}],
    }

    await runtime.write_files(
        lease,
        bootstrap,
        [
            {
                "directory": "/etc/niuu/guest-agent",
                "name": "config.json",
                "content": json.dumps(config),
            },
            {"directory": "/run/niuu/session/tokens/sa", "name": "token", "content": "/run/niuu/x"},
        ],
    )

    items = json.loads(_calls(runtime)[-1].kwargs["data"])
    assert items[0]["directory"] == "/var/run/niuu/host-sessions/0/guest-agent"
    assert json.loads(items[0]["content"]) == {
        "listen": {"address": "169.254.169.254", "port": 80},
        "token_volumes": [{"directory": "/var/run/niuu/slots/0/run/tokens/sa"}],
    }
    # A credential's own content is never rewritten.
    assert items[1] == {
        "directory": "/var/run/niuu/slots/0/run/tokens/sa",
        "name": "token",
        "content": "/run/niuu/x",
    }


async def test_warm_reaches_the_mac_and_fetches_the_bundle(mac, tmp_path):
    runtime, bootstrap, lease = mac
    runtime.prepare = AsyncMock()

    await runtime.warm(lease, bootstrap)

    runtime.prepare.assert_awaited_once()
    assert BUNDLE_MEDIA_TYPE in _program(_calls(runtime)[0])


async def test_stop_ends_the_jobs_archives_then_removes_the_slot_verified(mac):
    runtime, bootstrap, lease = mac

    await runtime.stop(lease, bootstrap)

    calls = _calls(runtime)
    commands = [_program(call) for call in calls]
    assert BUNDLE_MEDIA_TYPE in commands[0]
    calls, commands = calls[1:], commands[1:]
    assert commands[0] == runtime._python(
        programs.STOP,
        json.dumps(
            {
                "label": "world.niuu.slot.0",
                "state_root": "/var/run/niuu/host-sessions/0",
                "data_root": "/var/lib/niuu/slots/0/session",
                "marker": str(lease.id),
                "stop_timeout": 30,
                "poll": 2,
            }
        ),
    )
    assert calls[0].kwargs["expected_exit_codes"] == (0, 42)
    assert calls[0].kwargs["safe_error_prefix"] == STAGE_PREFIX
    assert commands[1] == runtime._python(
        programs.ARCHIVE,
        json.dumps(
            {
                "root": "/var/lib/niuu/slots/0/session",
                "excludes": [".allocation", "home/.codex/tmp"],
                "report": "/var/run/niuu/host-sessions/0/archive-unreadable.json",
            }
        ),
    )
    assert calls[1].kwargs["expected_exit_codes"] == (0, programs.ARCHIVE_SKIPPED)
    assert calls[1].kwargs["safe_detail_prefix"] == DETAIL_PREFIX
    assert commands[2] == runtime._python(programs.SLOT, "down")
    runtime._guest_access.close_tunnel.assert_awaited_once_with(str(lease.id))


async def test_a_stop_that_left_files_root_may_not_read_out_of_the_archive_says_so(
    mac, monkeypatch
):
    runtime, bootstrap, lease = mac
    left_out = ["./home/Library/Group Containers/group.com.apple.x/a", "./home/Library/Biome"]

    archive = runtime._python(
        programs.ARCHIVE,
        json.dumps(
            {
                "root": "/var/lib/niuu/slots/0/session",
                "excludes": [".allocation", "home/.codex/tmp"],
                "report": "/var/run/niuu/host-sessions/0/archive-unreadable.json",
            }
        ),
    )

    async def execute(_lease, _bootstrap, command, **kwargs):
        if command == archive:
            return GuestCommandResult(programs.ARCHIVE_SKIPPED, b"")
        if command == runtime._python(
            programs.ARCHIVE_REPORT, "/var/run/niuu/host-sessions/0/archive-unreadable.json"
        ):
            return GuestCommandResult(0, json.dumps(left_out).encode())
        return GuestCommandResult(0, json.dumps(DESCRIPTION).encode())

    runtime._guest_access.execute = AsyncMock(side_effect=execute)
    degraded = []
    monkeypatch.setattr(
        "volundr.adapters.outbound.ssh_darwin_host_runtime.record_degraded",
        lambda path, message: degraded.append((path, message)),
    )

    await runtime.stop(lease, bootstrap)

    commands = [_program(call) for call in _calls(runtime)]
    report = commands.index(
        runtime._python(
            programs.ARCHIVE_REPORT, "/var/run/niuu/host-sessions/0/archive-unreadable.json"
        )
    )
    # Read before the slot (and the report with it) is removed.
    assert commands[report + 1] == runtime._python(programs.SLOT, "down")
    [(path, message)] = degraded
    assert path == "compute.host.archive.unreadable"
    assert "leaves out 2 paths" in message
    assert "home/Library/Biome (1); home/Library/Group Containers (1)" in message


async def test_a_stop_fetches_the_bundle_before_running_its_programs(mac):
    """A start that failed before its bundle arrived must still be stoppable."""
    runtime, bootstrap, lease = mac

    await runtime.stop(lease, bootstrap)

    commands = [_program(call) for call in _calls(runtime)]
    install = SshGuestAccess.python_command(
        programs.INSTALL, runtime._image, BUNDLE_CACHE, BUNDLE_MEDIA_TYPE
    )
    # The install runs under the Mac's own python3; what follows needs the bundle's.
    assert commands[0] == install
    assert runtime._python_binary not in commands[0]
    assert runtime._python_binary in commands[1]


async def test_a_stop_whose_bundle_cannot_be_fetched_raises_and_leaves_the_slot(mac):
    runtime, bootstrap, lease = mac
    runtime._guest_access.execute = AsyncMock(side_effect=RuntimeError("HTTP 404"))

    with pytest.raises(RuntimeError, match="HTTP 404"):
        await runtime.stop(lease, bootstrap)

    assert len(_calls(runtime)) == 1
    runtime._guest_access.close_tunnel.assert_not_awaited()


async def test_a_stop_before_storage_was_prepared_keeps_the_previous_archive(mac):
    runtime, bootstrap, lease = mac
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(42, b""))
    delivery = AsyncMock()
    runtime._credential_delivery = delivery

    await runtime.stop(lease, bootstrap)

    commands = [_program(call) for call in _calls(runtime)]
    assert not any(" tar " in command for command in commands)
    assert commands[-1] == runtime._python(programs.SLOT, "down")
    delivery.stop.assert_awaited_once_with(lease, bootstrap)


# -- shared slots ------------------------------------------------------------------------------


@pytest.fixture
def shared_mac(tmp_path):
    runtime = _runtime(tmp_path, shared_slots=True)
    allocation = uuid4()
    session = _session()
    bootstrap = runtime.bootstrap(session, SPEC, MachineBootstrap())
    lease = SlotView(allocation, session.id, 1, _machine(allocation))
    runtime._guest_access.execute = AsyncMock(
        return_value=GuestCommandResult(0, json.dumps(DESCRIPTION).encode())
    )
    runtime._guest_access.open_tunnel = AsyncMock()
    runtime._guest_access.close_tunnel = AsyncMock()
    return runtime, bootstrap, lease


def test_a_mac_runs_one_slot_unless_the_operator_shares_them(tmp_path):
    assert not _runtime(tmp_path).supports_slots
    assert _runtime(tmp_path).slot_platforms == frozenset()
    shared = _runtime(tmp_path, shared_slots=True)
    assert shared.supports_slots
    assert shared.slot_platforms == {HostPlatform.DARWIN}


def test_shared_slots_need_a_delivery_each_slot_receives_its_own_credentials_from(tmp_path):
    runtime = _runtime(tmp_path, shared_slots=True)
    runtime._credential_delivery = type("OneIdentity", (), {"serves_slots": False})()
    assert not runtime.supports_slots
    runtime._credential_delivery = type("PerSlot", (), {"serves_slots": True})()
    assert runtime.supports_slots


async def test_a_shared_slot_starts_skuld_on_its_own_ports_with_no_fixed_path_links(shared_mac):
    runtime, bootstrap, lease = shared_mac

    await runtime.start(lease, bootstrap)

    calls = _calls(runtime)
    slot = json.loads(calls[2].kwargs["data"])
    assert (slot["name"], slot["uid"], slot["links"]) == ("niuu-slot-1", 61001, [])
    placed = json.loads(calls[-1].kwargs["data"])
    environment = placed["environment"]
    assert environment["SKULD__PORT"] == "8082"
    assert environment["SKULD__VOLUNDR_API_URL"] == "http://127.0.0.1:18081"
    assert placed["darwin"]["label"] == "world.niuu.slot.1"
    assert placed["darwin"]["links"] == []


def test_a_shared_slots_tunnel_reaches_its_own_broker_and_callback_ports(shared_mac):
    runtime, _, lease = shared_mac

    assert runtime._tunnel_ends(runtime._layout(lease)) == {
        "remote_port": 8082,
        "reverse_bind_host": "127.0.0.1",
        "reverse_port": 18081,
        "key": f"{lease.id}-slot-1",
    }


async def test_a_shared_slots_tunnel_is_opened_to_its_own_broker(shared_mac):
    runtime, bootstrap, lease = shared_mac

    await runtime.target(lease, bootstrap)

    kwargs = runtime._guest_access.open_tunnel.await_args.kwargs
    assert (kwargs["remote_port"], kwargs["reverse_port"]) == (8082, 18081)


async def test_a_shared_slots_guest_agent_listens_on_the_slots_own_address(shared_mac):
    runtime, bootstrap, lease = shared_mac
    config = {
        "metadata_url": "https://forge.example/api/v1/internal/compute/metadata/credentials",
        "slot": 1,
        "listen": {"address": "169.254.169.254", "port": 80},
        "token_volumes": [{"directory": "/etc/niuu/guest-agent/tokens"}],
    }

    await runtime.write_files(
        lease,
        bootstrap,
        [
            {
                "directory": "/etc/niuu/guest-agent",
                "name": "config.json",
                "content": json.dumps(config),
            },
            {"directory": "/etc/niuu/guest-agent", "name": "notes", "content": "not json"},
        ],
    )

    data = json.loads(_calls(runtime)[0].kwargs["data"])
    written = json.loads(data[0]["content"]) if isinstance(data, list) else None
    assert written is not None
    assert written["listen"] == {"address": "169.254.169.253", "port": 80}
    assert written["token_volumes"][0]["directory"].startswith("/var/run/niuu/host-sessions/1/")
    # Any other file of the agent's is only moved to the Mac's places.
    assert data[1]["content"] == "not json"


def test_a_shared_slot_removes_only_its_own_metadata_address(shared_mac):
    runtime, bootstrap, lease = shared_mac
    layout = runtime._layout(lease)

    spec = runtime._slot_spec(layout, {"metadata_address": "169.254.169.254"})

    assert spec["metadata_address"] == "169.254.169.253"
    assert runtime._slot_spec(layout, {})["metadata_address"] is None


def test_a_workflow_runs_unit_is_refused_on_a_mac_with_shared_slots(shared_mac):
    runtime, _, _ = shared_mac

    with pytest.raises(HostedUnitError, match="shared slots"):
        runtime._add_members(_session(), {}, [{"id": str(uuid4()), "port": 8090}])


def test_the_bundle_is_pruned_only_while_no_slot_runs_a_session(tmp_path):
    cache = tmp_path / "bundles"
    (cache / "old").mkdir(parents=True)
    (cache / "current").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launchctl = bin_dir / "launchctl"

    def prune(listing: str) -> list[str]:
        launchctl.write_text(f"#!/bin/sh\nprintf '%s' {shlex.quote(listing)}\n")
        launchctl.chmod(0o755)
        subprocess.run(
            [sys.executable, "-I", "-c", programs.PRUNE, str(cache), "current", "world.niuu.slot"],
            check=True,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )
        return sorted(entry.name for entry in cache.iterdir())

    header = "PID\tStatus\tLabel\n"
    assert prune(header + "17\t0\tworld.niuu.slot.3\n") == ["current", "old"]
    assert prune(header + "17\t0\tworld.niuu.slotted\n") == ["current"]


# -- the install program, against a fake registry ----------------------------------------------


def _bundle_archive(description: dict) -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        content = json.dumps(description).encode()
        info = tarfile.TarInfo("./bundle.json")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return data.getvalue()


FAKE_CURL = r"""#!PYTHON
import json, os, sys
args = sys.argv[1:]
def value(flag):
    return args[args.index(flag) + 1]
url, output, headers = args[-1], value('--output'), value('--dump-header')
auth = [a for a in args if a.startswith('Authorization: ')]
served = json.load(open(os.environ['FAKE_REGISTRY']))
open(os.environ['FAKE_LOG'], 'a').write(url + ' ' + ('auth' if auth else 'anon') + '\n')
if url.startswith('https://ghcr.io/token'):
    open(output, 'w').write(json.dumps({'token': 'anonymous'}))
    open(headers, 'w').write('')
    print('200', end='')
    sys.exit(0)
if not auth:
    open(output, 'w').write('')
    open(headers, 'w').write('HTTP/2 401\r\nwww-authenticate: Bearer realm="https://ghcr.io/token",'
        'service="ghcr.io",scope="repository:niuulabs/skuld-darwin:pull"\r\n')
    print('401', end='')
    sys.exit(0)
path = served.get(url.split('/v2/niuulabs/skuld-darwin/', 1)[1])
if path is None:
    print('404', end='')
    sys.exit(0)
open(output, 'wb').write(open(path, 'rb').read())
open(headers, 'w').write('')
print('200', end='')
"""


@pytest.fixture
def registry(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(FAKE_CURL.replace("PYTHON", sys.executable))
    curl.chmod(0o755)
    for tool in ("chown", "chmod"):
        # Root's ownership change is not this test's to make.
        (bin_dir / tool).write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / tool).chmod(0o755)
    served: dict[str, str] = {}
    (tmp_path / "served.json").write_text("{}")

    def serve(archive: bytes, *, media_type=BUNDLE_MEDIA_TYPE, layers=1):
        blob = tmp_path / "blob"
        blob.write_bytes(archive)
        layer = hashlib.sha256(archive).hexdigest()
        manifest = json.dumps(
            {"layers": [{"mediaType": media_type, "digest": f"sha256:{layer}"}] * layers}
        ).encode()
        digest = hashlib.sha256(manifest).hexdigest()
        (tmp_path / "manifest").write_bytes(manifest)
        served.update(
            {
                f"manifests/sha256:{digest}": str(tmp_path / "manifest"),
                f"blobs/sha256:{layer}": str(blob),
            }
        )
        (tmp_path / "served.json").write_text(json.dumps(served))
        return digest

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_REGISTRY": str(tmp_path / "served.json"),
        "FAKE_LOG": str(tmp_path / "log"),
    }
    return serve, env, tmp_path


def _install(env, digest, cache, tag=""):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            programs.INSTALL,
            f"ghcr.io/niuulabs/skuld-darwin{tag}@sha256:{digest}",
            str(cache),
            BUNDLE_MEDIA_TYPE,
        ],
        env=env,
        capture_output=True,
        text=True,
    )


def test_install_fetches_the_bundle_anonymously_verifies_and_unpacks_it(registry):
    serve, env, tmp_path = registry
    description = {**DESCRIPTION, "platform": f"darwin-{os.uname().machine}"}
    digest = serve(_bundle_archive(description))
    cache = tmp_path / "cache"

    result = _install(env, digest, cache)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert json.loads((cache / digest / "bundle.json").read_text()) == description
    assert [path.name for path in cache.iterdir()] == [digest]
    log = (tmp_path / "log").read_text().split("\n")
    assert log[0].endswith(f"manifests/sha256:{digest} anon")
    assert log[1].startswith("https://ghcr.io/token?service=ghcr.io&scope=repository:niuulabs")
    assert log[2].endswith(f"manifests/sha256:{digest} auth")
    # A second install reads the unpacked bundle and fetches nothing.
    before = (tmp_path / "log").read_text()
    assert _install(env, digest, cache).returncode == 0
    assert (tmp_path / "log").read_text() == before


def test_install_fetches_a_pin_that_names_its_tag_by_digest(registry):
    serve, env, tmp_path = registry
    description = {**DESCRIPTION, "platform": f"darwin-{os.uname().machine}"}
    digest = serve(_bundle_archive(description))

    result = _install(env, digest, tmp_path / "cache", tag=":dev-" + "e" * 40)

    assert result.returncode == 0, result.stderr
    assert f"/v2/niuulabs/skuld-darwin/manifests/sha256:{digest}" in (tmp_path / "log").read_text()


def test_install_refuses_a_bundle_whose_digest_the_registry_does_not_match(registry):
    serve, env, tmp_path = registry
    serve(_bundle_archive(DESCRIPTION))

    result = _install(env, "d" * 64, tmp_path / "cache")

    assert result.returncode != 0
    assert "answered HTTP 404" in result.stderr


def test_install_refuses_a_bundle_for_another_platform(registry):
    serve, env, tmp_path = registry
    digest = serve(_bundle_archive({"format": 1, "platform": "darwin-x86_128"}))

    result = _install(env, digest, tmp_path / "cache")

    assert result.returncode != 0
    assert "is a bundle for darwin-x86_128" in result.stderr
    assert not (tmp_path / "cache" / digest).exists()


def test_install_refuses_an_artifact_without_exactly_one_bundle_layer(registry):
    serve, env, tmp_path = registry
    digest = serve(_bundle_archive(DESCRIPTION), layers=2)

    result = _install(env, digest, tmp_path / "cache")

    assert result.returncode != 0
    assert "holds 2 layers" in result.stderr


def test_install_needs_a_digest_pinned_reference(registry):
    _, env, tmp_path = registry

    result = subprocess.run(
        [sys.executable, "-c", programs.INSTALL, "ghcr.io/x/y:dev", str(tmp_path), "t"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert "must be pinned by digest" in result.stderr


async def test_the_slot_is_never_touched_without_the_sessions_launch(mac):
    runtime, _, lease = mac

    with pytest.raises(ValueError, match="no session launch in its bootstrap"):
        await runtime._supervise(lease, MachineBootstrap(), runtime._layout(lease), "down")


def test_the_slot_programs_find_every_job_of_the_slot_on_the_mac(tmp_path):
    shims = tmp_path / "bin"
    shims.mkdir()
    (shims / "launchctl").write_text(
        '#!/bin/sh\nprintf "PID\\tStatus\\tLabel\\n1\\t0\\tworld.niuu.slot.0.agent\\n'
        '2\\t0\\tworld.niuu.slot.01\\n3\\t0\\tworld.niuu.slot.0\\n"\n'
    )
    (shims / "launchctl").chmod(0o755)
    daemons = tmp_path / "LaunchDaemons"
    daemons.mkdir()
    (daemons / "world.niuu.slot.0.left.plist").write_text("")
    program = (
        programs._SLOT_COMMON.replace("/Library/LaunchDaemons", str(daemons))
        + "\nprint(json.dumps(jobs('world.niuu.slot.0')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        env={"PATH": f"{shims}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    # The session first; another slot's job is never the slot's.
    assert json.loads(result.stdout) == [
        "world.niuu.slot.0",
        "world.niuu.slot.0.agent",
        "world.niuu.slot.0.left",
    ]


# -- the archive program, against a real tree --------------------------------------------------


def _archive(tmp_path, root, *, env=None):
    spec = {
        "root": str(root),
        "excludes": [".allocation", "home/.codex/tmp"],
        "report": str(tmp_path / "state" / "archive-unreadable.json"),
    }
    return subprocess.run(
        [sys.executable, "-I", "-c", programs.ARCHIVE, json.dumps(spec)],
        capture_output=True,
        env=env,
    )


def _storage(tmp_path) -> Path:
    root = tmp_path / "session"
    (root / "workspace" / "src").mkdir(parents=True)
    (root / "workspace" / "src" / "main.py").write_text("print('hi')\n")
    (root / "home" / ".codex" / "tmp").mkdir(parents=True)
    (root / "home" / ".codex" / "tmp" / "scratch").write_text("x")
    (root / "home" / "notes").symlink_to("../workspace/src/main.py")
    (root / ".allocation").write_text("marker")
    return root


def test_the_archive_program_archives_the_storage_but_its_exclusions(tmp_path):
    root = _storage(tmp_path)

    result = _archive(tmp_path, root)

    assert result.returncode == 0, result.stderr
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        names = set(archive.getnames())
        assert archive.getmember("./home/notes").issym()
    assert {"./workspace/src/main.py", "./home/notes", "./home/.codex"} <= names
    assert not any(n.startswith(("./home/.codex/tmp", "./.allocation")) for n in names)
    assert not (tmp_path / "state").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files whatever their mode")
def test_the_archive_program_leaves_out_what_it_may_not_read_and_lists_it(tmp_path):
    root = _storage(tmp_path)
    guarded = root / "home" / "Library" / "Group Containers" / "group.com.apple.x"
    guarded.mkdir(parents=True)
    (guarded / "cache").write_text("x")
    (root / "home" / "Library" / "vault").write_text("x")
    (root / "home" / "Library" / "vault").chmod(0)
    guarded.chmod(0)
    try:
        result = _archive(tmp_path, root)
    finally:
        guarded.chmod(0o700)

    assert result.returncode == programs.ARCHIVE_SKIPPED, result.stderr
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        names = set(archive.getnames())
    assert "./workspace/src/main.py" in names
    assert not any("group.com.apple.x" in n or n.endswith("vault") for n in names)
    report = tmp_path / "state" / "archive-unreadable.json"
    assert json.loads(report.read_text()) == [
        "./home/Library/Group Containers/group.com.apple.x",
        "./home/Library/vault",
    ]
    shown = subprocess.run(
        [sys.executable, "-I", "-c", programs.ARCHIVE_REPORT, str(report)],
        capture_output=True,
        text=True,
    )
    assert json.loads(shown.stdout) == [
        "./home/Library/Group Containers/group.com.apple.x",
        "./home/Library/vault",
    ]
    assert not report.exists()


def test_the_archive_program_fails_with_tars_own_words(tmp_path):
    root = _storage(tmp_path)
    shims = tmp_path / "bin"
    shims.mkdir()
    (shims / "tar").write_text("#!/bin/sh\necho 'tar: ./workspace/big: Read error' >&2\nexit 1\n")
    (shims / "tar").chmod(0o755)

    result = _archive(tmp_path, root, env={"PATH": f"{shims}:/usr/bin:/bin"})

    assert result.returncode == 1
    stage, detail = _reported(result.stderr.decode())
    assert stage == "host-archive"
    assert detail == (
        f"tar exited with status 1 archiving {root}: tar: ./workspace/big: Read error"
    )


def _reported(stderr: str) -> tuple[str, str]:
    lines = stderr.splitlines()
    stage = next(line for line in lines if line.startswith(STAGE_PREFIX))
    detail = next(line for line in lines if line.startswith(DETAIL_PREFIX))
    return (
        stage.removeprefix(STAGE_PREFIX),
        base64.b64decode(detail.removeprefix(DETAIL_PREFIX)).decode(),
    )


def test_an_unforeseen_failure_of_the_stop_program_names_its_stage(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-c", programs.STOP, "not json"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    stage, detail = _reported(result.stderr)
    assert stage == "host-stop"
    assert detail.startswith("JSONDecodeError while stopping the session")
