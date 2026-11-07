"""SshHostVmRuntime: sessions directly on the VM, inside their slot's boundary.

The controller side runs against a recording guest access. The guest programs it ships
(image fetch and unpack, the pod containers' units) are proven in
``test_linux_guest_programs``.
"""

import ast
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

from tests.test_adapters.test_ssh_vm_runtime_slots import ISOLATION, SlotView, _keys
from volundr.adapters.outbound import linux_guest_programs as programs
from volundr.adapters.outbound import ssh_host_vm_runtime as module
from volundr.adapters.outbound import ssh_vm_runtime
from volundr.adapters.outbound.host_checks import AUDIT_SH
from volundr.adapters.outbound.ssh_host_vm_runtime import SshHostVmRuntime
from volundr.adapters.outbound.ssh_vm_runtime import SlotNotCleanError
from volundr.domain.compute import Machine, MachineBootstrap, MachineState
from volundr.domain.guest_access import GuestCommandResult
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec
from volundr.domain.session_runtimes import SessionRuntimeKind
from volundr.domain.vm_runtime import SessionImages

IMAGE = "ghcr.io/niuulabs/openshell@sha256:" + "a" * 64
DIGEST = "a" * 64
#: What ``linux_guest_programs.IMAGE`` prints for the session image (its ``host.json``).
UNPACKED = {
    "venv": "/opt/niuu",
    "python_root": "/sandbox/.uv/python",
    "digest": f"sha256:{DIGEST}",
    "reference": IMAGE,
    "env": [],
    "user": "",
    "workdir": "",
    "entrypoint": [],
    "cmd": [],
}
#: The same as the runtime hands it on, with where it was unpacked.
FETCHED = {
    **UNPACKED,
    "root": f"/var/lib/niuu/host-images/{DIGEST}",
    "rootfs": f"/var/lib/niuu/host-images/{DIGEST}/rootfs",
}
#: The commands the session takes from its image: the agent CLIs and the fallback tools.
_SESSION_IMAGE_WRAPPERS = {
    "agents": module.AGENT_COMMANDS,
    "tools": module.FALLBACK_TOOLS,
    "view": module.HOST_IMAGE_VIEW,
}
AGENT = "ghcr.io/niuulabs/guest-agent@sha256:" + "b" * 64
OPENBAO = "quay.io/openbao/openbao@sha256:" + "c" * 64


def _description(reference: str) -> dict:
    """What the image program prints for a pod image (a plain image, no relocatable Python)."""
    digest = "d" * 64 if reference == AGENT else "e" * 64
    return {
        "digest": f"sha256:{digest}",
        "reference": reference,
        "env": ["PATH=/usr/bin:/bin"],
        "user": "1000",
        "workdir": "/app",
        "entrypoint": ["/agent"],
        "cmd": ["serve"],
    }


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
    options.update(kwargs)
    if options["skuld_image"] is None:
        # Not configured: the release's session image is the one to run.
        del options["skuld_image"]
    return SshHostVmRuntime(**options)


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
        if not command.startswith(runtime._python(programs.IMAGE) + " "):
            return GuestCommandResult(0, b"{}")
        reference = shlex.split(command)[-3]
        description = UNPACKED if reference == IMAGE else _description(reference)
        return GuestCommandResult(0, json.dumps(description).encode())

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


def test_host_mode_needs_no_root_daemon_and_reuses_cleaned_slots(tmp_path):
    runtime = _runtime(tmp_path)

    assert runtime.requires_root_daemon is False
    assert runtime.supports_reuse is True


def test_the_releases_session_image_stands_in_for_skuld_image(tmp_path):
    runtime = _runtime(tmp_path, skuld_image=None, session_images=SessionImages(linux=IMAGE))

    assert runtime._image == IMAGE
    assert runtime.session_runtime is SessionRuntimeKind.HOST


def test_the_releases_session_image_and_a_configured_one_are_not_both_accepted(tmp_path):
    with pytest.raises(ValueError, match="remove skuld_image to run the release's"):
        _runtime(tmp_path, session_images=SessionImages(linux=IMAGE))


@pytest.mark.parametrize(
    "images", [None, SessionImages(), SessionImages(darwin="ghcr.io/niuulabs/x@sha256:" + "b" * 64)]
)
def test_host_mode_needs_a_session_image_from_one_place_or_the_other(tmp_path, images):
    with pytest.raises(ValueError, match="compute.session_images.linux"):
        _runtime(tmp_path, skuld_image=None, session_images=images)


def test_a_slot_has_no_holder_container_and_names_its_units_after_itself(host):
    runtime, bootstrap, lease = host

    layout = runtime._slot(lease)

    assert layout.supervisor_spec(image=IMAGE, platform_port=18080)["holder"] is None
    assert layout.unit_prefix == "niuu-slot-2-"


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
    commands = [_program(call) for call in calls]
    assert commands[0] == runtime._python(runtime._supervisor, "up")
    # The slot keeps no holder container: its namespace is the supervisor's own.
    assert json.loads(calls[0].kwargs["data"])["holder"] is None
    pods = json.loads(calls[3].kwargs["data"])
    assert commands[3] == runtime._python(programs.PODS_START)
    assert "containers_only" not in pods and pods["pods"]["images"] == {}
    assert commands[4] == runtime._python(
        programs.IMAGE, IMAGE, "/var/lib/niuu/host-images", json.dumps(_SESSION_IMAGE_WRAPPERS)
    )
    assert commands[5] == runtime._python(module._HOST_START)
    started = json.loads(calls[5].kwargs["data"])
    host_spec = started["host"]
    key = f"{lease.id}-slot-2"
    assert host_spec["pidfile"] == f"/run/niuu/host-sessions/{key}.pid"
    assert host_spec["log"] == f"/var/log/niuu/host-sessions/{key}.log"
    assert host_spec["user"] == "niuu-slot-2"
    # Skuld runs in a delegated systemd scope of the slot's slice, named per start.
    assert host_spec["scope_prefix"] == "niuu-slot-2-session-"
    assert started["slot"]["slice"] == "niuu_slot_2.slice"
    # Slot 2's own subordinate ids, its runtime directory, and the VM's credentials hidden.
    assert host_spec["containers"] == {"first": 1_000_000 + 2 * 65536, "count": 65536}
    assert host_spec["keep_owners"] == [
        "home/.local/share/containers",
        "home/.cache/containers",
    ]
    assert host_spec["masked"] == ["/etc/niuu"]
    assert host_spec["bin"] == "/usr/local/lib/niuu/host-bin"
    assert host_spec["init"] == module._HOST_INIT and host_spec["boot"] == module.SESSION_BOOT
    mounts = {item["target"]: item for item in host_spec["mounts"]}
    assert mounts["/opt/niuu-skuld"] == {
        "source": f"/var/lib/niuu/host-images/{DIGEST}",
        "target": "/opt/niuu-skuld",
        "readonly": True,
    }
    assert mounts["/opt/niuu"]["source"] == f"/var/lib/niuu/host-images/{DIGEST}/rootfs/opt/niuu"
    assert mounts["/sandbox/.uv/python"]["readonly"] is True
    assert mounts["/etc/resolv.conf"]["source"] == "/run/systemd/resolve/resolv.conf"
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
    # Nothing in a daemonless start asks a container engine for anything.
    assert not [command for command in commands if "docker" in command]


def _pod_payload() -> dict:
    """What a launch carries beyond Skuld: the credential agent's init and its sidecars."""
    agent = {
        "name": "guest-agent-init",
        "image": AGENT,
        "command": [],
        "args": [],
        "environment": {"MODE": "init"},
        "mounts": [{"source": "/etc/niuu/slots/2/guest-agent", "target": "/etc/niuu/guest-agent"}],
    }
    return {
        "environment": {},
        "init_containers": [agent],
        "sidecars": [
            {**agent, "name": "guest-agent"},
            {**agent, "name": "openbao-agent", "image": OPENBAO},
        ],
    }


async def test_a_launch_with_agents_fetches_each_distinct_pod_image_once_before_starting_them(host):
    runtime, bootstrap, lease = host
    steps = []
    runtime.report_startup_to(lambda sid, key, label: steps.append(key))
    payload = _pod_payload()

    await runtime._launch_skuld(lease, bootstrap, payload)

    calls = _calls(runtime)
    commands = [_program(call) for call in calls]
    fetched = [shlex.split(command)[-3] for command in commands[:2]]
    # In order of first use, the guest agent image once for its init container and sidecar.
    assert fetched == [AGENT, OPENBAO]
    for command in commands[:2]:
        assert command.startswith(runtime._python(programs.IMAGE) + " ")
        assert shlex.split(command)[-2:] == ["/var/lib/niuu/host-images", "null"]
    assert commands[2] == runtime._python(programs.PODS_START)
    sent = json.loads(calls[2].kwargs["data"])
    assert sent["init_containers"] == payload["init_containers"]
    assert sent["sidecars"] == payload["sidecars"]
    described = sent["pods"]["images"]
    assert list(described) == [AGENT, OPENBAO]
    assert described[AGENT] == {
        "rootfs": f"/var/lib/niuu/host-images/{'d' * 64}/rootfs",
        "env": ["PATH=/usr/bin:/bin"],
        "user": "1000",
        "workdir": "/app",
        "entrypoint": ["/agent"],
        "cmd": ["serve"],
    }
    assert described[OPENBAO]["rootfs"] == f"/var/lib/niuu/host-images/{'e' * 64}/rootfs"
    assert commands[3].startswith(runtime._python(programs.IMAGE) + " ")
    assert commands[4] == runtime._python(module._HOST_START)
    assert steps == ["guest_images", "guest_skuld", "guest_image", "guest_host"]


async def test_a_launch_without_agents_fetches_no_pod_image(host):
    runtime, bootstrap, lease = host
    steps = []
    runtime.report_startup_to(lambda sid, key, label: steps.append(key))

    await runtime._launch_skuld(lease, bootstrap, {"environment": {}})

    assert "guest_images" not in steps
    assert len(_calls(runtime)) == 3  # the pods program, the session image, the session


def test_the_pods_spec_names_the_slots_units_state_and_images(host):
    runtime, _, lease = host
    layout = runtime._slot(lease)
    image = {**_description(AGENT), "rootfs": "/r", "root": "/x", "digest": "sha256:" + "d" * 64}

    spec = runtime._pods_spec(layout, {AGENT: image})

    key = f"{lease.id}-slot-2"
    assert spec == {
        "prefix": "niuu-slot-2-",
        "slice": "niuu_slot_2.slice",
        "state": f"/run/niuu/host-sessions/{key}-pods",
        "logs": f"/var/log/niuu/host-sessions/{key}",
        "work": f"/run/niuu/host-pods/{key}",
        "resolv_conf": "/run/systemd/resolve/resolv.conf",
        "program": programs.CONTAINER,
        "images": {
            AGENT: {
                "rootfs": "/r",
                "env": ["PATH=/usr/bin:/bin"],
                "user": "1000",
                "workdir": "/app",
                "entrypoint": ["/agent"],
                "cmd": ["serve"],
            }
        },
        "stop_timeout": 30,
        "poll": 2,
        "start_timeout": 30,
    }


async def test_a_fetched_image_is_described_with_its_unpacked_root(host):
    runtime, bootstrap, lease = host

    fetched = await runtime._fetch_image(lease, bootstrap, AGENT)

    assert fetched == {
        **_description(AGENT),
        "root": f"/var/lib/niuu/host-images/{'d' * 64}",
        "rootfs": f"/var/lib/niuu/host-images/{'d' * 64}/rootfs",
    }


@pytest.mark.parametrize("name", ["_HOST_INIT", "_HOST_START", "_HOST_STOP"])
def test_the_host_programs_parse(name):
    ast.parse(getattr(module, name))


def test_the_session_starts_in_a_delegated_scope_not_beside_a_holder_container():
    start, init = module._HOST_START, module._HOST_INIT

    # Skuld's init is started by systemd-run into the slot's slice, with its cgroup delegated.
    assert "'systemd-run', '--scope'" in start
    assert "'--slice', s['slice']" in start and "'Delegate=yes'" in start
    assert "h['scope_prefix']" in start
    # The init finds its scope from its own cgroup, never from a container's process.
    assert "/proc/self/cgroup" in init
    for program in (start, init):
        assert "docker inspect" not in program and "s['holder']" not in program
    assert "cgroup_procs" not in init and "cgroup_procs" not in start


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

    spec = runtime._host_spec(runtime._slot(lease), payload, FETCHED)

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
        FETCHED,
    )

    assert (spec["containers"], spec["keep_owners"]) == (None, [])
    assert "XDG_RUNTIME_DIR" not in spec["environment"]


async def test_stop_ends_the_host_session_before_archiving_and_removing_the_slot(host):
    runtime, bootstrap, lease = host

    await runtime.stop(lease, bootstrap)

    calls = _calls(runtime)
    commands = [_program(call) for call in calls]
    key = f"{lease.id}-slot-2"
    assert commands[0] == runtime._python(
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
    # Then the agents, after Skuld has flushed through them.
    assert commands[1] == runtime._python(
        programs.PODS_STOP,
        json.dumps(
            {"prefix": "niuu-slot-2-", "data_root": "/var/lib/niuu/slots/2/session", "marker": key}
        ),
    )
    assert calls[1].kwargs["expected_exit_codes"] == (0, 42)
    assert "-C /var/lib/niuu/slots/2/session" in commands[2]
    assert commands[3] == runtime._python(runtime._supervisor, "limits")
    assert commands[4] == runtime._python(runtime._supervisor, "down")
    assert len(commands) == 5
    runtime._guest_access.close_tunnel.assert_awaited_once()
    # No container engine is asked to stop or remove anything.
    assert not [command for command in commands if "docker" in command]


async def test_stop_stops_the_credential_delivery_between_the_agents_and_the_archive(host):
    runtime, bootstrap, lease = host
    order = []
    delivery = AsyncMock()
    delivery.serves_slots = True
    delivery.stop = AsyncMock(side_effect=lambda *_: order.append("delivery"))
    runtime.configure_credential_delivery(delivery)
    original = runtime._guest_access.execute.side_effect

    async def record(lease_, bootstrap_, command, **kwargs):
        order.append("archive" if " tar " in command else "guest")
        return await original(lease_, bootstrap_, command, **kwargs)

    runtime._guest_access.execute.side_effect = record

    await runtime.stop(lease, bootstrap)

    delivery.stop.assert_awaited_once_with(lease, bootstrap)
    assert order[:4] == ["guest", "guest", "delivery", "archive"]


async def test_a_session_cancelled_before_its_storage_was_prepared_keeps_the_previous_archive(host):
    runtime, bootstrap, lease = host
    original = runtime._guest_access.execute.side_effect
    pods_stop = runtime._python(programs.PODS_STOP)

    async def execute(lease_, bootstrap_, command, **kwargs):
        if command.startswith(pods_stop + " "):
            return GuestCommandResult(42, b"")
        return await original(lease_, bootstrap_, command, **kwargs)

    runtime._guest_access.execute.side_effect = execute

    await runtime.stop(lease, bootstrap)

    commands = [_program(call) for call in _calls(runtime)]
    assert not [command for command in commands if " tar " in command]
    # The slot is still reported and removed, and the tunnel closed.
    assert commands[-2:] == [
        runtime._python(runtime._supervisor, "limits"),
        runtime._python(runtime._supervisor, "down"),
    ]
    runtime._guest_access.close_tunnel.assert_awaited_once()


async def test_warm_prepares_the_machine_and_unpacks_the_session_image_without_a_daemon(host):
    runtime, bootstrap, lease = host

    await runtime.warm(lease, bootstrap)

    commands = [_program(call) for call in _calls(runtime)]
    assert not [command for command in commands if "docker" in command]
    assert commands[-1].startswith(runtime._python(programs.IMAGE) + " ")
    assert shlex.split(commands[-1])[-3:] == [
        IMAGE,
        "/var/lib/niuu/host-images",
        json.dumps(_SESSION_IMAGE_WRAPPERS),
    ]


# -- recycling a stopped slot ------------------------------------------------------------------


async def test_recycle_verifies_the_removed_slot_then_audits_it_alone(host):
    runtime, bootstrap, lease = host

    await runtime.recycle(lease, bootstrap)

    calls = _calls(runtime)
    assert [_program(call) for call in calls[:1]] == [
        runtime._python(runtime._supervisor, "verify")
    ]
    audit = calls[1]
    assert _program(audit) == "sudo -n sh -s -- 61000 3 '' 0 check 2"
    assert audit.kwargs["data"] == AUDIT_SH.encode()
    assert len(calls) == 2


async def test_a_slot_the_supervisor_cannot_verify_is_not_audited_or_reused(host):
    runtime, bootstrap, lease = host
    original = runtime._guest_access.execute.side_effect

    async def execute(lease_, bootstrap_, command, **kwargs):
        if command == runtime._python(runtime._supervisor, "verify"):
            raise RuntimeError("Slot 2 teardown left: user niuu-slot-2")
        return await original(lease_, bootstrap_, command, **kwargs)

    runtime._guest_access.execute.side_effect = execute

    with pytest.raises(RuntimeError, match="teardown left"):
        await runtime.recycle(lease, bootstrap)

    assert len(_calls(runtime)) == 1


async def test_a_slot_whose_audit_finds_anything_is_not_recycled(host):
    runtime, bootstrap, lease = host
    original = runtime._guest_access.execute.side_effect

    async def execute(lease_, bootstrap_, command, **kwargs):
        if command.startswith("sudo -n sh -s"):
            return GuestCommandResult(0, b"finding /tmp/x is owned by uid 61002\n")
        return await original(lease_, bootstrap_, command, **kwargs)

    runtime._guest_access.execute.side_effect = execute

    with pytest.raises(SlotNotCleanError, match="/tmp/x is owned by uid 61002"):
        await runtime.recycle(lease, bootstrap)


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
