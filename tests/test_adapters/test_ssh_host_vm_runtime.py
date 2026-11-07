"""SshHostVmRuntime: sessions directly on the VM, inside their slot's boundary.

The controller side runs against a recording guest access; the image unpack runs
for real against a fake ``docker`` that exports a small image tree.
"""

import io
import json
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_adapters.test_ssh_vm_runtime_slots import ISOLATION, SlotView, _keys
from volundr.adapters.outbound import ssh_host_vm_runtime as module
from volundr.adapters.outbound import ssh_vm_runtime
from volundr.adapters.outbound.ssh_host_vm_runtime import SshHostVmRuntime
from volundr.domain.compute import Machine, MachineBootstrap, MachineState
from volundr.domain.guest_access import GuestCommandResult
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec
from volundr.domain.session_runtimes import SessionRuntimeKind

IMAGE = "ghcr.io/niuulabs/openshell@sha256:" + "a" * 64
UNPACKED = {"venv": "/opt/niuu", "python_root": "/sandbox/.uv/python", "digest": "a" * 64}


def _runtime(tmp_path: Path, **kwargs) -> SshHostVmRuntime:
    private, public = _keys(tmp_path)
    options = {
        "ssh_private_key_file": str(private),
        "ssh_public_key_file": str(public),
        "data_dir": str(tmp_path / "data"),
        "skuld_image": IMAGE,
        "slot_isolation": {**ISOLATION, "image_user": "sandbox"},
        "command_timeout_seconds": 0.05,
    }
    return SshHostVmRuntime(**{**options, **kwargs})


def _session() -> Session:
    return Session(name="test", owner_id="owner", tenant_id="tenant", source=GitSource())


SPEC = SessionSpec(values={"session": {"systemPrompt": "Be brief."}}, pod_spec=PodSpecAdditions())


def _launch(bootstrap: MachineBootstrap) -> dict:
    return json.loads(
        next(f.content for f in bootstrap.files if f.path == "/etc/niuu/session-launch.json")
    )


@pytest.fixture
def host(tmp_path):
    runtime = _runtime(tmp_path)
    allocation = uuid4()
    machine = Machine(
        allocation_id=allocation,
        resource_id="vm",
        state=MachineState.RUNNING,
        addresses=("192.0.2.10",),
    )
    session = _session()
    bootstrap = runtime.bootstrap(session, SPEC, MachineBootstrap())
    lease = SlotView(allocation, session.id, 2, machine)

    async def execute(lease_, bootstrap_, command, **kwargs):
        if command.startswith(runtime._python(module._UNPACK, IMAGE)[:60]) and IMAGE in command:
            return GuestCommandResult(0, json.dumps(UNPACKED).encode())
        return GuestCommandResult(0, b"{}")

    runtime._guest_access.execute = AsyncMock(side_effect=execute)
    runtime._guest_access.open_tunnel = AsyncMock()
    runtime._guest_access.close_tunnel = AsyncMock()
    return runtime, bootstrap, lease


def _calls(runtime):
    return runtime._guest_access.execute.await_args_list


def _program(call) -> str:
    return call.args[2]


# -- configuration -----------------------------------------------------------------------------


def test_host_mode_needs_separated_slots(tmp_path):
    with pytest.raises(ValueError, match="slot_isolation"):
        _runtime(tmp_path, slot_isolation=None)


def test_host_mode_needs_a_digest_pinned_image(tmp_path):
    with pytest.raises(ValueError, match="pin skuld_image"):
        _runtime(tmp_path, skuld_image="ghcr.io/niuulabs/openshell:dev")


def test_host_mode_needs_a_positive_start_timeout(tmp_path):
    with pytest.raises(ValueError, match="session_start_timeout_seconds"):
        _runtime(tmp_path, session_start_timeout_seconds=0)


def test_host_mode_offers_no_grants_and_runs_as_host(tmp_path):
    runtime = _runtime(tmp_path)

    assert runtime.session_runtime is SessionRuntimeKind.HOST
    assert runtime.session_grants == ()
    with pytest.raises(TypeError):
        _runtime(tmp_path, grants=["sudo"])


def test_container_storage_is_never_archived(tmp_path):
    assert _runtime(tmp_path)._archive_excludes == (
        "home/.codex/tmp",
        "home/.local/share/containers",
        "home/.cache/containers",
    )
    assert _runtime(tmp_path, containers=None)._archive_excludes == ("home/.codex/tmp",)


def test_subordinate_ids_must_fit_and_miss_the_slots_own_uids(tmp_path):
    isolation = {**ISOLATION, "image_user": "sandbox", "uid_base": 200_000}
    with pytest.raises(ValueError, match="overlap the slots' own uids"):
        _runtime(tmp_path, slot_isolation=isolation, containers={"subordinate_id_base": 150_000})
    with pytest.raises(ValueError, match="end past"):
        _runtime(tmp_path, containers={"subordinate_id_base": 2**32 - 2**20})
    with pytest.raises(ValueError):
        _runtime(tmp_path, containers={"subordinate_id_base": 99_999})
    with pytest.raises(ValueError):
        _runtime(tmp_path, containers={"subordinates": 1})


# -- the session's bootstrap -------------------------------------------------------------------


def test_the_session_runs_with_pinned_agents_first_then_nix_then_the_vm(tmp_path):
    runtime = _runtime(tmp_path)

    launch = _launch(runtime.bootstrap(_session(), SPEC, MachineBootstrap()))

    environment = launch["environment"]
    assert environment["PATH"].split(":") == [
        "/opt/niuu-skuld/agents",
        "/usr/local/lib/niuu/host-bin",
        "/home/skuld/.nix-profile/bin",
        "/nix/var/nix/profiles/default/bin",
        "/usr/local/sbin",
        "/usr/local/bin",
        "/usr/sbin",
        "/usr/bin",
        "/sbin",
        "/bin",
        "/opt/niuu-skuld/tools",
    ]
    assert environment["NIX_REMOTE"] == "daemon"
    assert environment["HOME"] == "/home/skuld"
    prompt = environment["SKULD__SESSION__SYSTEM_PROMPT"]
    assert prompt.startswith("Be brief.\n\n")
    assert "nix profile install nixpkgs#<package>" in prompt
    assert "apt and sudo are not available." in prompt
    assert "rootless with Podman" in prompt
    assert "grants" not in launch


def test_without_nix_the_agent_is_only_told_where_it_runs(tmp_path):
    runtime = _runtime(tmp_path, nix=False)

    environment = _launch(runtime.bootstrap(_session(), SPEC, MachineBootstrap()))["environment"]

    assert "nix" not in environment["PATH"]
    assert "NIX_REMOTE" not in environment
    assert "Nix" not in environment["SKULD__SESSION__SYSTEM_PROMPT"]


def test_without_containers_there_is_no_docker_shim_or_hint(tmp_path):
    runtime = _runtime(tmp_path, containers=None)

    environment = _launch(runtime.bootstrap(_session(), SPEC, MachineBootstrap()))["environment"]

    assert "/usr/local/lib/niuu/host-bin" not in environment["PATH"]
    assert "Podman" not in environment["SKULD__SESSION__SYSTEM_PROMPT"]


def test_a_session_without_a_system_prompt_gets_the_hint_alone(tmp_path):
    runtime = _runtime(tmp_path)
    spec = SessionSpec(values={"session": {}}, pod_spec=PodSpecAdditions())

    environment = _launch(runtime.bootstrap(_session(), spec, MachineBootstrap()))["environment"]

    assert environment["SKULD__SESSION__SYSTEM_PROMPT"].startswith("You are running directly")


# -- start and stop ----------------------------------------------------------------------------


async def test_start_runs_the_slot_agents_unpacks_skuld_then_starts_it_as_the_slot_user(host):
    runtime, bootstrap, lease = host
    steps = []
    runtime.report_startup_to(lambda sid, key, label: steps.append(key))

    await runtime.start(lease, bootstrap)

    calls = _calls(runtime)
    programs = [_program(call) for call in calls]
    assert programs[0] == runtime._python(runtime._supervisor, "up")
    containers = json.loads(calls[3].kwargs["data"])
    assert programs[3] == runtime._python(ssh_vm_runtime._START)
    assert containers["containers_only"] is True
    assert containers["slot"]["holder"] == "niuu-slot-2-holder"
    assert IMAGE in programs[4]
    assert programs[5] == runtime._python(module._HOST_START)
    started = json.loads(calls[5].kwargs["data"])
    host_spec = started["host"]
    key = f"{lease.id}-slot-2"
    assert host_spec["pidfile"] == f"/run/niuu/host-sessions/{key}.pid"
    assert host_spec["log"] == f"/var/log/niuu/host-sessions/{key}.log"
    assert host_spec["user"] == "niuu-slot-2"
    # Slot 2's own subordinate ids, its runtime directory, and the VM's credentials hidden.
    assert host_spec["containers"] == {"first": 1_000_000 + 2 * 65536, "count": 65536}
    assert host_spec["keep_owners"] == [
        "home/.local/share/containers",
        "home/.cache/containers",
    ]
    assert host_spec["masked"] == ["/etc/niuu"]
    assert host_spec["bin"] == "/usr/local/lib/niuu/host-bin"
    assert host_spec["init"] == module._HOST_INIT and host_spec["boot"] == module._BOOT
    mounts = {item["target"]: item for item in host_spec["mounts"]}
    assert mounts["/opt/niuu-skuld"] == {
        "source": f"/var/lib/niuu/host-images/{'a' * 64}",
        "target": "/opt/niuu-skuld",
        "readonly": True,
    }
    assert mounts["/opt/niuu"]["source"].endswith("/rootfs/opt/niuu")
    assert mounts["/sandbox/.uv/python"]["readonly"] is True
    assert mounts["/workspace"]["source"] == "/var/lib/niuu/slots/2/session/workspace"
    assert mounts["/home/skuld"]["readonly"] is False
    assert mounts["/tmp"]["source"] == mounts["/var/tmp"]["source"] == "/var/lib/niuu/slots/2/tmp"
    assert mounts["/dev/shm"]["source"] == "/run/niuu/slots/2/shm"
    assert host_spec["private"] == [
        "/var/lib/niuu/slots/2/tmp",
        "/run/niuu/slots/2/shm",
        "/run/niuu/slots/2/xdg",
    ]
    environment = started["environment"]
    assert environment["XDG_RUNTIME_DIR"] == "/run/niuu/slots/2/xdg"
    assert environment["NIUU_SKULD_PYTHON"] == "/opt/niuu/bin/python"
    assert "unprivileged user niuu-slot-2" in environment["SKULD__SESSION__SYSTEM_PROMPT"]
    assert environment["SKULD__HOST"] == "0.0.0.0"
    assert steps == [
        "guest_slot",
        "guest_files",
        "guest_tunnel",
        "guest_skuld",
        "guest_image",
        "guest_host",
    ]


async def test_the_pod_mounts_keep_their_targets_parents_first(host):
    runtime, bootstrap, lease = host
    payload = {
        "secret_mounts": [{"source": "/etc/niuu/slot/secret-0", "target": "/run/secrets/a/b"}],
        "volume_mounts": [
            {
                "source": "/run/niuu/slots/2/volumes/x",
                "target": "/var/run/secrets",
                "readonly": False,
            }
        ],
        "environment": {},
    }

    spec = runtime._host_spec(runtime._slot(lease), payload, UNPACKED)

    declared = spec["mounts"][-2:]
    # The image's /var/run is /run, as the VM's is.
    assert [(m["target"], m["readonly"]) for m in declared] == [
        ("/run/secrets", False),
        ("/run/secrets/a/b", True),
    ]


async def test_without_containers_a_slot_gets_no_subordinate_ids(tmp_path):
    runtime = _runtime(tmp_path, containers=None)
    machine = Machine(allocation_id=uuid4(), resource_id="vm", state=MachineState.RUNNING)

    spec = runtime._host_spec(
        runtime._slot(SlotView(machine.allocation_id, uuid4(), 1, machine)),
        {"environment": {}},
        UNPACKED,
    )

    assert (spec["containers"], spec["keep_owners"]) == (None, [])
    assert "XDG_RUNTIME_DIR" not in spec["environment"]


async def test_stop_ends_the_host_session_before_archiving_and_removing_the_slot(host):
    runtime, bootstrap, lease = host

    await runtime.stop(lease, bootstrap)

    programs = [_program(call) for call in _calls(runtime)]
    key = f"{lease.id}-slot-2"
    assert programs[0] == runtime._python(
        module._HOST_STOP,
        json.dumps(
            {
                "pidfile": f"/run/niuu/host-sessions/{key}.pid",
                "netns": "niuu-slot-2",
                "user": "niuu-slot-2",
                "containers": {"first": 1_000_000 + 2 * 65536, "count": 65536},
                "stop_timeout": 30,
                "poll": 2,
            }
        ),
    )
    assert programs[1].startswith(runtime._python(ssh_vm_runtime._STOP, str(lease.id))[:40])
    assert "-C /var/lib/niuu/slots/2/session" in programs[2]
    assert programs[-1] == runtime._python(runtime._supervisor, "down")


async def test_warm_pulls_then_unpacks_the_session_image(host):
    runtime, bootstrap, lease = host

    await runtime.warm(lease, bootstrap)

    programs = [_program(call) for call in _calls(runtime)]
    assert "docker pull" in programs[-2]
    assert IMAGE in programs[-1] and "/var/lib/niuu/host-images" in programs[-1]


# -- the image unpack, run for real ------------------------------------------------------------


def _image_tree() -> bytes:
    """An image with a relocatable Python, a native CLI and a node script CLI."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:

        def add(name, content=b"", mode=0o755, link=None):
            info = tarfile.TarInfo(name)
            if link is not None:
                info.type, info.linkname = tarfile.SYMTYPE, link
            elif content is None:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
            else:
                info.size, info.mode = len(content), mode
            archive.addfile(info, io.BytesIO(content or b"") if info.isreg() else None)

        add("sandbox/.uv/python/cpython-3.14.10-linux-x86_64-gnu/bin/python3.14", b"\x7fELF")
        add(
            "sandbox/.uv/python/cpython-3.14-linux-x86_64-gnu",
            link="cpython-3.14.10-linux-x86_64-gnu",
        )
        add(
            "opt/niuu/bin/python",
            link="/sandbox/.uv/python/cpython-3.14-linux-x86_64-gnu/bin/python3.14",
        )
        add("opt/tools/claude.exe", b"\x7fELF")
        add("usr/local/bin/claude", link="/opt/tools/claude.exe")
        add("usr/bin/node", b"\x7fELF")
        add("usr/lib/codex/codex.js", b"#!/usr/bin/env node\nconsole.log(1)\n")
        add("usr/bin/codex", link="../lib/codex/codex.js")
        add("dev/null", b"")
    return buffer.getvalue()


def _fake_docker(tmp_path: Path, env: list[str]) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    image_tar = tmp_path / "image.tar"
    image_tar.write_bytes(_image_tree())
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        f"""#!{sys.executable}
import json, sys
args = sys.argv[1:]
open({str(log)!r}, 'a').write(' '.join(args) + '\\n')
if args[:2] == ['image', 'inspect']:
    print(json.dumps([{{'Config': {{'Env': {env!r}}}}}]))
elif args[0] == 'create':
    print('container-1')
elif args[0] == 'export':
    sys.stdout.buffer.write(open({str(image_tar)!r}, 'rb').read())
"""
    )
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _unpack(tmp_path: Path, env: list[str]) -> subprocess.CompletedProcess:
    bin_dir = _fake_docker(tmp_path, env)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            module._UNPACK,
            IMAGE,
            str(tmp_path / "cache"),
            json.dumps(["claude", "codex", "grok"]),
            json.dumps(["node", "gh"]),
            "/opt/niuu-skuld",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )


ENV = [
    "PATH=/opt/niuu/bin:/usr/local/bin:/usr/bin:/bin",
    "VIRTUAL_ENV=/opt/niuu",
    "UV_PYTHON_INSTALL_DIR=/sandbox/.uv/python",
]


def test_unpack_extracts_the_image_once_and_wraps_its_commands(tmp_path):
    result = _unpack(tmp_path, ENV)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == UNPACKED
    root = tmp_path / "cache" / ("a" * 64)
    assert (root / "rootfs/opt/niuu/bin/python").is_symlink()
    assert not (root / "rootfs/dev/null").exists()
    claude = (root / "agents/claude").read_text()
    assert claude == '#!/bin/sh\nexec /opt/niuu-skuld/rootfs/opt/tools/claude.exe "$@"\n'
    # A node script runs on the image's node, whatever node the VM has.
    codex = (root / "agents/codex").read_text()
    assert codex == (
        "#!/bin/sh\nexec /opt/niuu-skuld/rootfs/usr/bin/node "
        '/opt/niuu-skuld/rootfs/usr/lib/codex/codex.js "$@"\n'
    )
    assert not (root / "agents/grok").exists()
    assert (root / "tools/node").exists() and not (root / "tools/gh").exists()

    second = subprocess.run(
        [sys.executable, "-c", module._UNPACK, IMAGE, str(tmp_path / "cache"), "[]", "[]", "/v"],
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0 and json.loads(second.stdout) == UNPACKED


def test_unpack_refuses_an_image_on_the_distributions_python(tmp_path):
    result = _unpack(tmp_path, ["PATH=/opt/venv/bin:/usr/bin", "VIRTUAL_ENV=/opt/venv"])

    assert result.returncode != 0
    assert "relocatable Python" in result.stderr
    assert not (tmp_path / "cache" / ("a" * 64)).exists()


def test_unpack_refuses_a_venv_whose_python_is_outside_its_python_root(tmp_path):
    result = _unpack(tmp_path, [*ENV[:2], "UV_PYTHON_INSTALL_DIR=/usr/lib/python"])

    assert result.returncode != 0
    assert "does not run on its own Python" in result.stderr


async def test_a_host_session_restores_its_links_to_absolute_paths(host):
    runtime, bootstrap, lease = host

    await runtime.start(lease, bootstrap)

    prepare = _program(_calls(runtime)[2])
    assert prepare == runtime._python(
        ssh_vm_runtime._PREPARE,
        f"{lease.id}-slot-2",
        "empty",
        '["home/.codex/tmp", "home/.local/share/containers", "home/.cache/containers"]',
        "/var/lib/niuu/slots/2/session",
        "absolute-links",
    )


def _restore(tmp_path: Path, *flags: str) -> subprocess.CompletedProcess:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        link = tarfile.TarInfo("./home/.nix-profile")
        link.type, link.linkname = tarfile.SYMTYPE, "/home/skuld/.local/state/nix/profiles/profile"
        archive.addfile(link)
        hard = tarfile.TarInfo("./home/shadow")
        hard.type, hard.linkname = tarfile.LNKTYPE, "/etc/shadow"
        archive.addfile(hard)
    root = tmp_path / "session"
    return subprocess.run(
        [
            sys.executable,
            "-c",
            ssh_vm_runtime._PREPARE,
            "key",
            "restore",
            "[]",
            str(root),
            *flags,
        ],
        input=buffer.getvalue(),
        capture_output=True,
    )


def test_absolute_symbolic_links_restore_only_where_asked_and_hard_links_never(tmp_path):
    kept = _restore(tmp_path / "host", "absolute-links")
    refused = _restore(tmp_path / "container")

    # The hard link to a path outside the session still fails the restore.
    assert kept.returncode != 0 and b"shadow" in kept.stderr
    assert os.readlink(tmp_path / "host/session/home/.nix-profile") == (
        "/home/skuld/.local/state/nix/profiles/profile"
    )
    assert refused.returncode != 0 and b"AbsoluteLinkError" in refused.stderr
    assert not (tmp_path / "container/session/home/.nix-profile").is_symlink()


def test_host_mode_serves_slots_only_with_a_delivery_that_serves_them(tmp_path):
    runtime = _runtime(tmp_path)
    assert runtime.supports_slots is True

    delivery = AsyncMock()
    delivery.serves_slots = False
    runtime.configure_credential_delivery(delivery)

    assert runtime.supports_slots is False


# -- what root writes for a host slot stays out of the slot's reach ---------------------------


def test_root_written_slot_state_lives_in_the_slots_root_owned_directory(host):
    runtime, _, lease = host

    layout = runtime._slot(lease)

    assert isinstance(layout, module.HostSlotLayout)
    assert layout.relocate("/etc/niuu/session-launch.json") == (
        "/etc/niuu/slots/2/session-launch.json"
    )
    assert layout.relocate("/etc/niuu/session-secrets/0") == "/etc/niuu/slots/2/session-secrets/0"
    assert layout.relocate("/etc/niuu/forge-session") == "/etc/niuu/slots/2/forge-session"
    assert layout.relocate("/etc/niuu/guest-agent") == "/etc/niuu/slots/2/guest-agent"
    assert layout.relocate("/run/niuu/session/tokens/sa") == "/etc/niuu/slots/2/run/tokens/sa"
    # Only the session's own storage is in the directory the slot owns.
    assert layout.relocate("/var/lib/niuu/session/home") == "/var/lib/niuu/slots/2/session/home"


async def test_host_session_files_are_written_where_the_slot_cannot_redirect_them(host):
    runtime, bootstrap, lease = host

    await runtime.start(lease, bootstrap)

    files = json.loads(_calls(runtime)[1].kwargs["data"])
    assert [item["path"] for item in files] == ["/etc/niuu/slots/2/session-launch.json"]


def _prepare(root: Path, *args: str, archive: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", ssh_vm_runtime._PREPARE, "key", *args, "[]", str(root)],
        input=archive,
        capture_output=True,
    )


def test_prepare_never_writes_through_a_marker_the_slot_turned_into_a_link(tmp_path):
    root, victim = tmp_path / "session", tmp_path / "victim"
    root.mkdir()
    (root / ".allocation").symlink_to(victim)

    result = _prepare(root, "empty")

    assert result.returncode != 0
    assert not victim.exists()


def test_prepare_refuses_storage_the_slot_turned_into_a_link(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "session").symlink_to(elsewhere)

    result = _prepare(tmp_path / "session", "empty")

    assert result.returncode != 0
    assert list(elsewhere.iterdir()) == []


def test_prepare_marks_fresh_storage_once_and_checks_it_after(tmp_path):
    root = tmp_path / "session"

    assert _prepare(root, "empty").returncode == 0
    assert (root / ".allocation").read_text() == "key"
    assert _prepare(root, "empty").returncode == 0
    (root / ".allocation").write_text("other")
    assert b"allocation mismatch" in _prepare(root, "empty").stderr
