"""SshHostVmRuntime hosting a workflow run's unit: the root and members as processes of one slot.

Every Skuld of the unit is its own PID 1 (``_HOST_START``) in the slot's network namespace,
cgroup and user; they share the workspace and the mesh directory, and keep their own home,
PID file, log and Forge session credential.
"""

import json
import os
import shlex
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_adapters.test_ssh_host_vm_runtime import (
    IMAGE,
    PRUNED,
    UNPACKED,
    _runtime,
)
from tests.test_adapters.test_vm_guest_credentials import FakeTokens
from tests.test_adapters.test_vm_hosted_unit import (
    credential,
    launch_of,
    member_launch,
    root_session,
    slot_lease,
    unit_spec,
)
from volundr.adapters.outbound import linux_guest_programs as programs
from volundr.adapters.outbound import ssh_host_vm_runtime as module
from volundr.adapters.outbound.guest_credential_delivery import SshPushCredentialDelivery
from volundr.adapters.outbound.vm_hosted_unit import (
    FORGE_SESSIONS_DIRECTORY,
    UNIT_MESH_SOURCE,
    UNIT_MESH_TARGET,
    HostedUnitError,
)
from volundr.domain.compute import MachineBootstrap
from volundr.domain.guest_access import GuestCommandResult


@pytest.fixture
def unit(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.configure_credential_delivery(
        SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    )
    root = root_session()
    members = [
        member_launch(root, 8082, persona="coder"),
        member_launch(root, 8083, persona="reviewer"),
    ]
    spec = unit_spec(root, members)
    bootstrap = runtime.bootstrap(root, spec, MachineBootstrap())
    lease = slot_lease(root.id, slot=2)

    async def execute(lease_, bootstrap_, command, **kwargs):
        if command.startswith(runtime._python(programs.PRUNE) + " "):
            return GuestCommandResult(0, json.dumps(PRUNED).encode())
        if command.startswith(runtime._python(programs.IMAGE) + " "):
            assert shlex.split(command)[-4] == IMAGE
            return GuestCommandResult(0, json.dumps(UNPACKED).encode())
        return GuestCommandResult(0, b"{}")

    runtime._guest_access.execute = AsyncMock(side_effect=execute)
    runtime._guest_access.open_tunnel = AsyncMock()
    runtime._guest_access.close_tunnel = AsyncMock()
    runtime.stage_forge_session(root.id, credential(root.id))
    for member in members:
        runtime.stage_forge_session(member.session.id, member.spec.forge_session)
    return SimpleNamespace(
        runtime=runtime, root=root, members=members, bootstrap=bootstrap, lease=lease
    )


def calls(runtime):
    return runtime._guest_access.execute.await_args_list


def started(runtime, program: str) -> list[dict]:
    wanted = runtime._python(program)
    return [json.loads(c.kwargs["data"]) for c in calls(runtime) if c.args[2] == wanted]


async def test_start_starts_the_root_and_each_member_as_a_process_of_the_slot(unit):
    runtime, lease = unit.runtime, unit.lease

    await runtime.start(lease, unit.bootstrap)

    root, coder, reviewer = started(runtime, module._HOST_START)
    key = f"{lease.id}-slot-2"
    assert root["host"]["pidfile"] == f"/run/niuu/host-sessions/{key}.pid"
    assert coder["host"]["pidfile"] == f"/run/niuu/host-sessions/{key}-m8082.pid"
    assert reviewer["host"]["log"] == f"/var/log/niuu/host-sessions/{key}-m8083.log"
    # Each a scope of its own in the slot's slice, named for the member.
    assert root["host"]["scope_prefix"] == "niuu-slot-2-session-"
    assert coder["host"]["scope_prefix"] == "niuu-slot-2-m8082-session-"
    # One slot: one user, one namespace, one workspace; each Skuld its own broker port.
    assert {p["host"]["user"] for p in (root, coder, reviewer)} == {"niuu-slot-2"}
    assert root["environment"]["SKULD__PORT"] == "8081"
    assert coder["environment"]["SKULD__PORT"] == "8082"
    assert reviewer["environment"]["SESSION_ID"] == str(unit.members[1].session.id)


async def test_each_process_keeps_its_own_home_and_credential_and_shares_workspace_and_mesh(unit):
    runtime, lease = unit.runtime, unit.lease

    await runtime.start(lease, unit.bootstrap)

    root, coder, _ = started(runtime, module._HOST_START)
    data = "/var/lib/niuu/slots/2/session"

    def mounted(payload: dict, target: str) -> dict:
        return next(m for m in payload["host"]["mounts"] if m["target"] == target)

    assert mounted(root, "/home/skuld")["source"] == f"{data}/home"
    assert mounted(coder, "/home/skuld")["source"] == f"{data}/members/8082/home"
    for payload in (root, coder):
        assert mounted(payload, "/workspace")["source"] == f"{data}/workspace"
        assert mounted(payload, UNIT_MESH_TARGET)["source"] == "/etc/niuu/slots/2/run/mesh"
    member_id = coder["environment"]["SESSION_ID"]
    credential_mount = mounted(coder, "/run/niuu/forge-session")
    assert credential_mount["source"] == f"/etc/niuu/slots/2/forge-sessions/{member_id}"
    # The member's home is made by the slot's user, parents first.
    private = coder["host"]["private"]
    assert private[-3:] == [f"{data}/members", f"{data}/members/8082", f"{data}/members/8082/home"]
    assert root["host"]["private"] == private[:-3]


async def test_the_unit_is_prepared_once_and_only_the_roots_stop_withdraws_the_slots_ids(unit):
    runtime, lease = unit.runtime, unit.lease

    await runtime.start(lease, unit.bootstrap)

    programs_run = [c.args[2] for c in calls(runtime)]
    assert programs_run.count(runtime._python(programs.PODS_START)) == 1
    assert programs_run.count(runtime._python(module._HOST_START)) == 3


async def test_a_member_started_again_starts_only_its_own_process(unit):
    runtime, lease = unit.runtime, unit.lease
    coder = unit.members[0].session.id

    await runtime.restart_member(lease, unit.bootstrap, coder)

    programs_run = [c.args[2] for c in calls(runtime)]
    assert runtime._python(programs.PODS_START) not in programs_run
    assert not any(p.startswith(runtime._python(programs.PRUNE)) for p in programs_run)
    [payload] = started(runtime, module._HOST_START)
    assert payload["relaunch"] is True
    assert payload["environment"]["SESSION_ID"] == str(coder)
    assert payload["host"]["pidfile"].endswith("-m8082.pid")


async def test_a_launch_naming_no_process_of_the_unit_is_refused(unit):
    with pytest.raises(HostedUnitError, match="not Skuld processes of this unit"):
        unit.runtime._unit_payloads({**launch_of(unit.bootstrap), "only": ["stranger"]})


async def test_stopping_the_unit_stops_the_members_then_the_root_then_the_agents(unit):
    runtime, lease = unit.runtime, unit.lease

    await runtime.stop(lease, unit.bootstrap)

    stops = started_stops(runtime)
    key = f"{lease.id}-slot-2"
    assert [(s["pidfile"], s.get("member", False)) for s in stops] == [
        (f"/run/niuu/host-sessions/{key}-m8083.pid", True),
        (f"/run/niuu/host-sessions/{key}-m8082.pid", True),
        (f"/run/niuu/host-sessions/{key}.pid", False),
    ]
    order = [c.args[2] for c in calls(runtime)]
    pods_stop = next(
        i for i, p in enumerate(order) if p.startswith(runtime._python(programs.PODS_STOP))
    )
    last_stop = max(
        i for i, p in enumerate(order) if p.startswith(runtime._python(module._HOST_STOP))
    )
    assert last_stop < pods_stop


def started_stops(runtime) -> list[dict]:
    prefix = runtime._python(module._HOST_STOP) + " "
    return [
        json.loads(shlex.split(c.args[2])[-1])
        for c in calls(runtime)
        if c.args[2].startswith(prefix)
    ]


async def test_one_member_is_stopped_without_touching_the_slot(unit):
    runtime, lease = unit.runtime, unit.lease
    reviewer = unit.members[1].session.id

    await runtime.stop_member(lease, unit.bootstrap, reviewer)

    [stop] = started_stops(runtime)
    assert stop["member"] is True and stop["pidfile"].endswith("-m8083.pid")
    runtime._guest_access.close_tunnel.assert_awaited_once_with(f"{lease.id}-slot-2-m8083")


@pytest.mark.parametrize(("code", "running"), [(0, True), (44, False)])
async def test_a_members_process_is_running_when_it_holds_the_slots_namespace(unit, code, running):
    runtime = unit.runtime
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(code, b""))

    assert (
        await runtime.member_running(unit.lease, unit.bootstrap, unit.members[0].session.id)
        is running
    )

    program = runtime._guest_access.execute.await_args.args[2]
    assert program.startswith(runtime._python(module._HOST_RUNNING))
    assert runtime._guest_access.execute.await_args.kwargs["expected_exit_codes"] == (0, 44)


def test_the_running_program_answers_by_pidfile_and_namespace(tmp_path):
    spec = json.dumps({"pidfile": str(tmp_path / "none.pid"), "netns": "niuu-slot-9"})

    absent = subprocess.run([sys.executable, "-c", module._HOST_RUNNING, spec], env=os.environ)

    assert absent.returncode == 44
    (tmp_path / "bad.pid").write_text("not a pid")
    garbled = json.dumps({"pidfile": str(tmp_path / "bad.pid"), "netns": "niuu-slot-9"})
    assert subprocess.run([sys.executable, "-c", module._HOST_RUNNING, garbled]).returncode == 44


async def test_the_members_credentials_land_in_the_roots_directory_of_the_slot(unit):
    runtime, lease = unit.runtime, unit.lease

    await runtime.start(lease, unit.bootstrap)

    ssh_vm_runtime = sys.modules["volundr.adapters.outbound.ssh_vm_runtime"]
    written = [
        item
        for c in calls(runtime)
        if c.args[2] == runtime._python(ssh_vm_runtime._REFRESHED_FILES)
        for item in json.loads(c.kwargs["data"])
    ]
    member = unit.members[0].session.id
    [item] = [i for i in written if str(member) in i["directory"]]
    assert item["directory"] == f"/etc/niuu/slots/2/forge-sessions/{member}"
    assert not item["directory"].startswith(FORGE_SESSIONS_DIRECTORY)
    assert UNIT_MESH_SOURCE.startswith("/run/niuu/session")
