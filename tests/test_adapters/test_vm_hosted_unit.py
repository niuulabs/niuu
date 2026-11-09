"""A workflow run's unit on a VM: the root and every member in one machine or slot.

The runtime tests run the controller side against a recording guest access, and the
guest programs for real against fake ``docker`` binaries.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from niuu.forge_mcp.models import ForgeMcpGrant
from niuu.ports.session_proxy import SessionProxyTarget
from tests.test_adapters.test_vm_guest_credentials import FakeIdentity, FakeTokens
from volundr.adapters.outbound import ssh_vm_runtime as module
from volundr.adapters.outbound.guest_credential_delivery import (
    AGENT_CONFIG_DIRECTORY,
    MetadataCredentialDelivery,
    SshPushCredentialDelivery,
)
from volundr.adapters.outbound.local_process import (
    FORGE_MCP_GRANTS_ENV,
    FORGE_MCP_TOKEN_FILE_ENV,
)
from volundr.adapters.outbound.ssh_vm_runtime import SshContainerVmRuntime
from volundr.adapters.outbound.vm_hosted_unit import (
    FORGE_SESSIONS_DIRECTORY,
    HOSTED_MEMBERS_KEY,
    UNIT_MESH_SOURCE,
    UNIT_MESH_TARGET,
    HostedMemberLaunch,
    HostedUnitError,
    HostedUnitVmRuntime,
    member_entry,
    member_spec,
    require_same_pod,
)
from volundr.adapters.outbound.vm_slot_layout import SlotIsolation, SlotLayout
from volundr.domain.compute import (
    ComputeLease,
    ComputeSlot,
    Machine,
    MachineBootstrap,
    MachineState,
    SlotLease,
)
from volundr.domain.guest_access import GuestCommandResult
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec
from volundr.domain.services.host_access_key import generate_host_access_key
from volundr.domain.vm_runtime import VmRuntimeStageError

ISOLATION = {"cpu": 1.0, "memory_mib": 1024, "pids": 256}


def credential(session_id) -> IssuedForgeSessionToken:
    return IssuedForgeSessionToken(
        token=f"token-of-{session_id}",
        expires_at=0,
        session_id=str(session_id),
        launch_id="launch",
        scopes=("forge:notify",),
        grants=(ForgeMcpGrant.MESSAGE,),
    )


def root_session() -> Session:
    return Session(
        name="wf-run",
        owner_id="owner",
        tenant_id="tenant",
        source=GitSource(repo="https://git.example/r.git", branch="main"),
    )


def member_launch(
    unit: Session,
    port: int,
    *,
    persona: str,
    forge: bool = True,
    pod: PodSpecAdditions | None = None,
) -> HostedMemberLaunch:
    session = Session(
        id=uuid4(),
        name=f"wf-run-{persona}",
        owner_id="owner",
        tenant_id="tenant",
        source=unit.source,
        hosted_by=unit.id,
        hosted_port=port,
    )
    spec = SessionSpec(
        values={
            "session": {"persona": {"id": persona}, "systemPrompt": f"You are the {persona}."},
            "env": {"A_MEMBER": persona},
        },
        pod_spec=pod or PodSpecAdditions(),
        forge_session=credential(session.id) if forge else None,
    )
    return HostedMemberLaunch(session, spec)


def unit_spec(
    unit: Session, members: list[HostedMemberLaunch], *, forge: bool = True, pod=None
) -> SessionSpec:
    return SessionSpec(
        values={"session": {}, HOSTED_MEMBERS_KEY: [member_entry(m) for m in members]},
        pod_spec=pod or PodSpecAdditions(),
        forge_session=credential(unit.id) if forge else None,
    )


def launch_of(bootstrap: MachineBootstrap) -> dict:
    return json.loads(next(f.content for f in bootstrap.files if f.path == module._LAUNCH))


def slot_lease(session_id: UUID, *, slot: int = 0) -> SlotLease:
    allocation = uuid4()
    machine = ComputeLease(
        id=allocation,
        pool_id="pool",
        profile="small",
        request_fingerprint="test",
        slots=2,
        machine=Machine(
            allocation_id=allocation,
            resource_id="vm",
            state=MachineState.RUNNING,
            addresses=("192.0.2.10",),
        ),
    )
    binding = ComputeSlot(
        id=uuid4(),
        allocation_id=allocation,
        slot=slot,
        pool_id="pool",
        session_id=session_id,
        owner_id="owner",
        tenant_id="tenant",
    )
    return SlotLease(allocation=machine, binding=binding)


def make_runtime(tmp_path: Path, **kwargs) -> SshContainerVmRuntime:
    runtime = SshContainerVmRuntime(
        host_key=generate_host_access_key(),
        data_dir=str(tmp_path / "data"),
        skuld_image="test/skuld:dev",
        command_timeout_seconds=0.05,
        default_service_account="skuld-session",
        **kwargs,
    )
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(0, b"{}"))
    runtime._guest_access.open_tunnel = AsyncMock(
        return_value=SessionProxyTarget("http://127.0.0.1:9000", "127.0.0.1", 9000)
    )
    runtime._guest_access.close_tunnel = AsyncMock()
    return runtime


@pytest.fixture
def unit(tmp_path):
    """A unit with two members on a whole VM, credentials pushed by the controller."""
    runtime = make_runtime(tmp_path)
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
    return SimpleNamespace(
        runtime=runtime,
        root=root,
        members=members,
        spec=spec,
        bootstrap=bootstrap,
        lease=slot_lease(root.id),
    )


def stage(unit) -> None:
    unit.runtime.stage_forge_session(unit.root.id, credential(unit.root.id))
    for member in unit.members:
        unit.runtime.stage_forge_session(member.session.id, member.spec.forge_session)


def programs(runtime) -> list[str]:
    return [call.args[2] for call in runtime._guest_access.execute.await_args_list]


def payloads(runtime) -> list:
    return [call.kwargs.get("data") for call in runtime._guest_access.execute.await_args_list]


# -- what the pod manager hands the runtime ----------------------------------------------------


def test_a_member_reaches_the_runtime_as_plain_json_without_its_credential():
    root = root_session()
    member = member_launch(root, 8082, persona="coder")

    entry = member_entry(member)

    assert json.loads(json.dumps(entry)) == entry
    assert entry["id"] == str(member.session.id) and entry["port"] == 8082
    assert entry["forge_session"] is True
    assert member.spec.forge_session.token not in json.dumps(entry)
    rebuilt = member_spec(entry)
    assert rebuilt.values == member.spec.values and rebuilt.forge_session is None


def test_a_member_without_a_port_is_refused():
    root = root_session()
    member = member_launch(root, 8082, persona="coder")
    unported = HostedMemberLaunch(
        member.session.model_copy(update={"hosted_port": None}), member.spec
    )

    with pytest.raises(HostedUnitError, match="no broker port"):
        member_entry(unported)


@pytest.mark.parametrize(
    ("pod", "field"),
    [
        (PodSpecAdditions(volumes=({"name": "x", "emptyDir": {}},)), "volumes"),
        (PodSpecAdditions(service_account="other"), "service account"),
        (PodSpecAdditions(extra_containers=({"name": "s", "image": "i"},)), "extra containers"),
    ],
)
def test_a_member_with_another_pod_than_the_roots_is_refused(pod, field):
    root = root_session()
    entry = member_entry(member_launch(root, 8082, persona="coder", pod=pod))

    with pytest.raises(HostedUnitError, match=f"its own {field}"):
        require_same_pod(unit_spec(root, []), entry)


def test_a_member_with_the_roots_pod_is_accepted():
    root = root_session()
    pod = PodSpecAdditions(service_account="sa")
    entry = member_entry(member_launch(root, 8082, persona="coder", pod=pod))

    require_same_pod(unit_spec(root, [], pod=pod), entry)


# -- the launch payload ------------------------------------------------------------------------


def test_the_launch_carries_every_member_with_its_own_environment_port_and_credential(unit):
    launch = launch_of(unit.bootstrap)

    first, second = launch["members"]
    assert [m["id"] for m in launch["members"]] == [str(m.session.id) for m in unit.members]
    assert (first["port"], second["port"]) == (8082, 8083)
    assert launch["environment"]["SKULD__PORT"] == "8081"
    assert launch["environment"]["SESSION_ID"] == str(unit.root.id)
    for member, entry in zip(unit.members, launch["members"], strict=True):
        environment = entry["environment"]
        assert environment["SESSION_ID"] == str(member.session.id)
        assert environment["SKULD__SESSION__ID"] == str(member.session.id)
        assert environment["SKULD__PORT"] == str(entry["port"])
        assert environment["A_MEMBER"] == member.spec.values["env"]["A_MEMBER"]
        assert (
            json.loads(environment["SKULD__SESSION__PERSONA"])
            == member.spec.values["session"]["persona"]
        )
        assert environment[FORGE_MCP_TOKEN_FILE_ENV] == "/var/run/niuu/forge-session/token"
        assert entry["forge_session_directory"] == f"{FORGE_SESSIONS_DIRECTORY}/{member.session.id}"
        assert entry["forge_session"] is True


def test_each_skuld_of_the_unit_mounts_only_its_own_credential_directory(unit):
    launch = launch_of(unit.bootstrap)

    root_sources = {m["source"] for m in launch["volume_mounts"]}
    assert module._FORGE_SESSION_DIR in root_sources
    for entry in launch["members"]:
        sources = {m["source"] for m in entry["volume_mounts"]}
        assert entry["forge_session_directory"] in sources
        assert module._FORGE_SESSION_DIR not in sources
        others = {m["forge_session_directory"] for m in launch["members"] if m is not entry}
        assert not sources & others
    # The root mounts its own directory whole: no member's lives inside it.
    assert all(
        not m["forge_session_directory"].startswith(module._FORGE_SESSION_DIR + "/")
        for m in launch["members"]
    )


def test_the_unit_shares_one_mesh_directory_and_creates_every_directory(unit):
    launch = launch_of(unit.bootstrap)
    mesh = {"source": UNIT_MESH_SOURCE, "target": UNIT_MESH_TARGET, "readonly": False}

    assert mesh in launch["volume_mounts"]
    assert all(mesh in entry["volume_mounts"] for entry in launch["members"])
    paths = {d["path"]: d["mode"] for d in launch["directories"]}
    assert paths[UNIT_MESH_SOURCE] == "0777"
    for entry in launch["members"]:
        assert paths[entry["forge_session_directory"]] == "0755"


def test_the_brokers_of_the_unit_are_not_previews(tmp_path):
    runtime = make_runtime(tmp_path, preview_router_auth={"adapter": "x.Auth", "kwargs": {}})
    runtime.configure_credential_delivery(
        SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    )
    root = root_session()
    members = [member_launch(root, 8082, persona="a"), member_launch(root, 8083, persona="b")]

    launch = launch_of(runtime.bootstrap(root, unit_spec(root, members), MachineBootstrap()))

    def excluded(environment) -> list[int]:
        scanner = json.loads(environment["SKULD__PREVIEW__PORT_SCANNER"])
        return scanner["kwargs"]["excluded_ports"]

    assert {8082, 8083} <= set(excluded(launch["environment"]))
    first, second = launch["members"]
    assert {8081, 8083} <= set(excluded(first["environment"]))
    assert {8081, 8082} <= set(excluded(second["environment"]))
    assert 8082 not in excluded(first["environment"])


def test_a_member_on_the_roots_port_is_refused(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.configure_credential_delivery(
        SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    )
    root = root_session()
    members = [member_launch(root, 8081, persona="coder")]

    with pytest.raises(HostedUnitError, match="broker port 8081"):
        runtime.bootstrap(root, unit_spec(root, members), MachineBootstrap())


def test_two_members_on_one_port_are_refused(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.configure_credential_delivery(
        SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    )
    root = root_session()
    members = [member_launch(root, 8082, persona="a"), member_launch(root, 8082, persona="b")]

    with pytest.raises(HostedUnitError, match="broker port 8082"):
        runtime.bootstrap(root, unit_spec(root, members), MachineBootstrap())


def test_members_with_credentials_need_a_credential_delivery(tmp_path):
    runtime = make_runtime(tmp_path)
    root = root_session()
    members = [member_launch(root, 8082, persona="coder")]

    with pytest.raises(ValueError, match="compute.credential_delivery"):
        runtime.bootstrap(root, unit_spec(root, members, forge=False), MachineBootstrap())


def test_a_unit_without_members_launches_as_before(tmp_path):
    runtime = make_runtime(tmp_path)
    root = root_session()

    launch = launch_of(
        runtime.bootstrap(
            root,
            SessionSpec(values={"session": {}}, pod_spec=PodSpecAdditions()),
            MachineBootstrap(),
        )
    )

    assert "members" not in launch
    assert all(m["source"] != UNIT_MESH_SOURCE for m in launch["volume_mounts"])


def test_the_metadata_delivery_runs_an_agent_for_each_member(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.configure_credential_delivery(
        MetadataCredentialDelivery(
            metadata_url="https://forge.example", guest_identity=FakeIdentity()
        )
    )
    root = root_session()
    members = [member_launch(root, 8082, persona="a"), member_launch(root, 8083, persona="b")]

    launch = launch_of(runtime.bootstrap(root, unit_spec(root, members), MachineBootstrap()))

    names = [c["name"] for c in launch["sidecars"]]
    assert names[0] == "niuu-guest-agent"
    for member in members:
        suffix = str(member.session.id)[:8]
        [init] = [c for c in launch["init_containers"] if c["name"].endswith(suffix)]
        [serve] = [c for c in launch["sidecars"] if c["name"].endswith(suffix)]
        config = f"{AGENT_CONFIG_DIRECTORY}/{member.session.id}.json"
        assert init["name"] == f"niuu-guest-agent-init-{suffix}"
        assert init["command"][-2:] == ["--config", config]
        assert serve["command"][-2:] == ["--config", config]
        directory = f"{FORGE_SESSIONS_DIRECTORY}/{member.session.id}"
        assert {"source": directory, "target": directory, "readonly": False} in serve["mounts"]
    # The unit's own agent keeps the unit's configuration.
    [root_agent] = [c for c in launch["sidecars"] if c["name"] == "niuu-guest-agent"]
    assert root_agent["command"][-1] == f"{AGENT_CONFIG_DIRECTORY}/config.json"


def test_an_agent_container_without_a_config_argument_cannot_be_given_to_a_member():
    with pytest.raises(HostedUnitError, match="takes no --config"):
        SshContainerVmRuntime._member_agent_container(
            {"name": "agent", "command": ["serve"]}, "abcd1234", "/c.json"
        )


# -- starting the unit -------------------------------------------------------------------------


async def test_start_delivers_every_members_credential_into_its_own_directory(unit):
    stage(unit)

    await unit.runtime.start(unit.lease, unit.bootstrap)

    written = {}
    for call in unit.runtime._guest_access.execute.await_args_list:
        if call.args[2] == unit.runtime._python(module._REFRESHED_FILES):
            for item in json.loads(call.kwargs["data"]):
                written[item["directory"]] = item
    for member in unit.members:
        item = written[f"{FORGE_SESSIONS_DIRECTORY}/{member.session.id}"]
        assert item["content"] == f"token-of-{member.session.id}"
        assert item["name"] == "token"
    assert written[module._FORGE_SESSION_DIR]["content"] == f"token-of-{unit.root.id}"
    payload = json.loads(payloads(unit.runtime)[-1])
    assert payload["relaunch"] is True
    for entry in payload["members"]:
        assert entry["environment"][FORGE_MCP_GRANTS_ENV] == "message"
    assert "only" not in payload


async def test_a_unit_whose_member_credential_was_lost_does_not_start(unit):
    # A restarted controller replaying a start no longer holds the members' credentials,
    # and the guest does not hold them either.
    unit.runtime.stage_forge_session(unit.root.id, credential(unit.root.id))

    async def execute(lease, bootstrap, command, **kwargs):
        if command.startswith(unit.runtime._python(module._REQUIRE_FILE)):
            return GuestCommandResult(43, b"")
        return GuestCommandResult(0, b"{}")

    unit.runtime._guest_access.execute = AsyncMock(side_effect=execute)

    with pytest.raises(VmRuntimeStageError, match="forge-session"):
        await unit.runtime.start(unit.lease, unit.bootstrap)


async def test_the_metadata_delivery_writes_a_config_for_each_member(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.configure_credential_delivery(
        MetadataCredentialDelivery(
            metadata_url="https://forge.example", guest_identity=FakeIdentity()
        )
    )
    root = root_session()
    members = [member_launch(root, 8082, persona="a")]
    bootstrap = runtime.bootstrap(root, unit_spec(root, members), MachineBootstrap())

    await runtime.start(slot_lease(root.id), bootstrap)

    configs = {}
    for call in runtime._guest_access.execute.await_args_list:
        if call.args[2] == runtime._python(module._REFRESHED_FILES):
            for item in json.loads(call.kwargs["data"]):
                configs[item["name"]] = json.loads(item["content"])
    member_id = str(members[0].session.id)
    assert "session_id" not in configs["config.json"]
    assert configs[f"{member_id}.json"]["session_id"] == member_id
    assert configs[f"{member_id}.json"]["forge_session"] == {
        "directory": f"{FORGE_SESSIONS_DIRECTORY}/{member_id}",
        "file": "token",
    }
    assert configs[f"{member_id}.json"]["allocation_id"] == configs["config.json"]["allocation_id"]
    assert json.loads(payloads(runtime)[-1])["relaunch"] is True


async def test_member_files_leave_everything_else_a_delivery_writes_alone():
    files = AsyncMock()
    member = module._MemberFiles(files, "m1")

    await member.write_files(None, None, [{"directory": "/x", "name": "token", "content": "t"}])
    await member.file_exists(None, None, "/x/token")

    files.write_files.assert_awaited_once_with(
        None, None, [{"directory": "/x", "name": "token", "content": "t"}]
    )
    files.file_exists.assert_awaited_once_with(None, None, "/x/token")


async def test_a_member_starts_again_alone_under_its_new_credential(unit):
    stage(unit)
    coder = unit.members[0].session.id

    await unit.runtime.restart_member(unit.lease, unit.bootstrap, coder)

    payload = json.loads(payloads(unit.runtime)[-1])
    assert payload["only"] == [str(coder)] and payload["relaunch"] is True
    written = [
        item["directory"]
        for call in unit.runtime._guest_access.execute.await_args_list
        if call.args[2] == unit.runtime._python(module._REFRESHED_FILES)
        for item in json.loads(call.kwargs["data"])
    ]
    assert written == [f"{FORGE_SESSIONS_DIRECTORY}/{coder}"]


async def test_a_session_that_is_no_member_cannot_be_restarted_or_stopped(unit):
    stranger = uuid4()

    for operation in (unit.runtime.restart_member, unit.runtime.stop_member):
        with pytest.raises(HostedUnitError, match="not a member of the unit"):
            await operation(unit.lease, unit.bootstrap, stranger)


async def test_members_are_found_running_or_not_by_their_containers(unit):
    runtime, coder = unit.runtime, unit.members[0].session.id
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(0, b""))

    assert await runtime.member_running(unit.lease, unit.bootstrap, coder) is True
    program = runtime._guest_access.execute.await_args.args[2]
    assert program.startswith(runtime._python(module._MEMBER, "running", "niuu-skuld-m8082"))

    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(44, b""))
    assert await runtime.member_running(unit.lease, unit.bootstrap, coder) is False


async def test_stopping_a_member_stops_only_its_container_and_tunnel(unit):
    runtime, coder = unit.runtime, unit.members[0].session.id

    await runtime.stop_member(unit.lease, unit.bootstrap, coder)

    [call] = runtime._guest_access.execute.await_args_list
    assert call.args[2].startswith(runtime._python(module._MEMBER, "stop", "niuu-skuld-m8082"))
    runtime._guest_access.close_tunnel.assert_awaited_once_with(f"{unit.lease.id}-m8082")


async def test_a_member_is_reached_through_a_forward_of_its_own(unit):
    runtime, reviewer = unit.runtime, unit.members[1].session.id

    target = await runtime.member_target(unit.lease, unit.bootstrap, reviewer)

    assert target.connect_port == 9000
    kwargs = runtime._guest_access.open_tunnel.await_args.kwargs
    assert kwargs["remote_port"] == 8083
    assert kwargs["key"] == f"{unit.lease.id}-m8083"
    # The unit's own tunnel carries the callbacks; this one asks for a port of its own.
    assert kwargs["reverse_port"] == 0


async def test_a_members_broker_is_ready_when_its_health_check_answers(unit, monkeypatch):
    class Client:
        status = 200

        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            assert url == "http://127.0.0.1:9000/health"
            return SimpleNamespace(status_code=Client.status)

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    coder = unit.members[0].session.id

    assert await unit.runtime.member_ready(unit.lease, unit.bootstrap, coder) is True
    Client.status = 503
    assert await unit.runtime.member_ready(unit.lease, unit.bootstrap, coder) is False


async def test_stopping_the_unit_stops_its_members_first_and_closes_their_tunnels(unit):
    runtime = unit.runtime

    await runtime.stop(unit.lease, unit.bootstrap)

    stop = next(
        call
        for call in runtime._guest_access.execute.await_args_list
        if call.args[2].startswith(runtime._python(module._STOP))
    )
    assert stop.args[2] == runtime._python(
        module._STOP,
        str(unit.lease.id),
        "30",
        "null",
        json.dumps(["niuu-skuld-m8083", "niuu-skuld-m8082"]),
    )
    closed = [call.args[0] for call in runtime._guest_access.close_tunnel.await_args_list]
    assert closed == [f"{unit.lease.id}-m8082", f"{unit.lease.id}-m8083", str(unit.lease.id)]


def test_the_runtimes_that_host_a_unit_declare_it(tmp_path):
    assert isinstance(make_runtime(tmp_path), HostedUnitVmRuntime)


# -- a slot of a VM ----------------------------------------------------------------------------


@pytest.fixture
def slot_unit(tmp_path):
    runtime = make_runtime(tmp_path, slot_isolation=ISOLATION)
    runtime.configure_credential_delivery(
        SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    )
    root = root_session()
    members = [member_launch(root, 8082, persona="coder"), member_launch(root, 8083, persona="qa")]
    bootstrap = runtime.bootstrap(root, unit_spec(root, members), MachineBootstrap())
    return SimpleNamespace(
        runtime=runtime,
        root=root,
        members=members,
        bootstrap=bootstrap,
        lease=slot_lease(root.id, slot=1),
        layout=SlotLayout(config=SlotIsolation(**ISOLATION), allocation_id=uuid4(), slot=1),
    )


async def test_the_members_of_a_slot_listen_on_its_address_and_live_in_its_directories(slot_unit):
    stage_slot = slot_unit
    runtime = stage_slot.runtime
    runtime.stage_forge_session(slot_unit.root.id, credential(slot_unit.root.id))
    for member in slot_unit.members:
        runtime.stage_forge_session(member.session.id, member.spec.forge_session)

    await runtime.start(slot_unit.lease, slot_unit.bootstrap)

    launch = json.loads(payloads(runtime)[-1])
    for member, entry in zip(slot_unit.members, launch["members"], strict=True):
        environment = entry["environment"]
        assert environment["SKULD__HOST"] == "0.0.0.0"
        assert environment["SKULD__VOLUNDR_API_URL"] == "http://10.249.0.5:18080"
        credential_mount = next(
            m for m in entry["volume_mounts"] if m["target"] == "/var/run/niuu/forge-session"
        )
        assert credential_mount["source"] == (
            f"/var/lib/niuu/slots/1/etc/forge-sessions/{member.session.id}"
        )
    mesh = next(m for m in launch["volume_mounts"] if m["target"] == UNIT_MESH_TARGET)
    assert mesh["source"] == "/run/niuu/slots/1/mesh"
    paths = {d["path"] for d in launch["directories"]}
    assert "/run/niuu/slots/1/mesh" in paths
    written = [
        item["directory"]
        for call in runtime._guest_access.execute.await_args_list
        if call.args[2] == runtime._python(module._REFRESHED_FILES)
        for item in json.loads(call.kwargs["data"])
    ]
    assert f"/var/lib/niuu/slots/1/etc/forge-sessions/{slot_unit.members[0].session.id}" in written


async def test_a_member_of_a_slot_is_reached_at_the_slots_address(slot_unit):
    runtime = slot_unit.runtime

    await runtime.member_target(
        slot_unit.lease, slot_unit.bootstrap, slot_unit.members[1].session.id
    )

    kwargs = runtime._guest_access.open_tunnel.await_args.kwargs
    key = f"{slot_unit.lease.id}-slot-1"
    assert (kwargs["remote_host"], kwargs["remote_port"]) == ("10.249.0.6", 8083)
    assert kwargs["key"] == f"{key}-m8083" and kwargs["reverse_port"] == 0


async def test_stopping_a_slot_stops_its_members_by_the_slots_container_names(slot_unit):
    runtime = slot_unit.runtime

    await runtime.stop(slot_unit.lease, slot_unit.bootstrap)

    stop = next(
        call.args[2]
        for call in runtime._guest_access.execute.await_args_list
        if call.args[2].startswith(runtime._python(module._STOP))
    )
    assert json.dumps(["niuu-slot-1-skuld-m8083", "niuu-slot-1-skuld-m8082"]) in stop


# -- the guest programs, run against a fake docker ---------------------------------------------

_FAKE_DOCKER = r"""
import json, os, sys
name = os.path.basename(sys.argv[0])
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps([name, *sys.argv[1:]]) + '\n')
if name == 'docker' and sys.argv[1:3] == ['container', 'inspect']:
    containers = json.loads(os.environ.get('FAKE_CONTAINERS', '{}'))
    if sys.argv[3] not in containers:
        sys.exit(1)
    info = containers[sys.argv[3]]
    print(json.dumps([{'Config': {'Labels': info['labels']},
        'State': {'Running': info['running']}}]))
if name == 'docker' and sys.argv[1:3] == ['ps', '-aq']:
    print(os.environ.get('FAKE_PS', ''))
"""


@pytest.fixture
def docker(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker", "chown"):
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\n{_FAKE_DOCKER}")
        path.chmod(0o755)
    log = tmp_path / "commands.jsonl"

    def run(program: str, *args: str, payload: dict | None = None, **env):
        return subprocess.run(
            [sys.executable, "-c", program, *args],
            input=json.dumps(payload).encode() if payload is not None else None,
            capture_output=True,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "FAKE_LOG": str(log),
                **env,
            },
        )

    def commands() -> list[list[str]]:
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return SimpleNamespace(run=run, commands=commands)


async def start_payload(unit, tmp_path) -> dict:
    stage(unit)
    await unit.runtime.start(unit.lease, unit.bootstrap)
    payload = json.loads(payloads(unit.runtime)[-1])
    # The guest creates these under /run and /etc; the program is not run as root here.
    payload["directories"] = []
    payload["repo"] = ""
    root = tmp_path / "vm"
    root.mkdir(exist_ok=True)
    (root / ".allocation").write_text(payload["allocation_id"])
    return payload


def start_program(tmp_path) -> str:
    return module._START.replace("'/var/lib/niuu/session'", repr(str(tmp_path / "vm")))


async def test_the_start_program_runs_the_root_and_every_member_with_their_own_homes(
    unit, docker, tmp_path
):
    payload = await start_payload(unit, tmp_path)

    completed = docker.run(start_program(tmp_path), payload=payload)

    assert completed.returncode == 0, completed.stderr
    runs = [c for c in docker.commands() if c[:2] == ["docker", "run"]]
    assert [r[r.index("--name") + 1] for r in runs] == [
        "niuu-skuld",
        "niuu-skuld-m8082",
        "niuu-skuld-m8083",
    ]
    root = tmp_path / "vm"
    for run, home in zip(
        runs,
        (root / "home", root / "members/8082/home", root / "members/8083/home"),
        strict=True,
    ):
        assert f"type=bind,src={home},dst=/home/skuld" in run
        assert f"type=bind,src={root / 'workspace'},dst=/workspace" in run
        assert home.is_dir()
    coder = unit.members[0].session.id
    assert any(
        arg.startswith(f"type=bind,src={FORGE_SESSIONS_DIRECTORY}/{coder},dst=") for arg in runs[1]
    )
    assert not any(FORGE_SESSIONS_DIRECTORY in arg for arg in runs[0])
    assert any(arg == f"type=bind,src={UNIT_MESH_SOURCE},dst={UNIT_MESH_TARGET}" for arg in runs[2])


async def test_a_unit_that_already_exists_is_started_again_in_place(unit, docker, tmp_path):
    payload = await start_payload(unit, tmp_path)
    labels = {"compute.niuu.io/allocation": payload["allocation_id"]}
    existing = {
        name: {"labels": labels, "running": True}
        for name in ("niuu-skuld", "niuu-skuld-m8082", "niuu-skuld-m8083")
    }

    completed = docker.run(
        start_program(tmp_path), payload=payload, FAKE_CONTAINERS=json.dumps(existing)
    )

    assert completed.returncode == 0, completed.stderr
    restarts = [c for c in docker.commands() if c[:2] == ["docker", "restart"]]
    assert [c[2] for c in restarts] == ["niuu-skuld", "niuu-skuld-m8082", "niuu-skuld-m8083"]
    assert not [c for c in docker.commands() if c[:2] == ["docker", "run"]]


async def test_a_unit_started_part_way_starts_only_the_containers_it_lacks(unit, docker, tmp_path):
    payload = await start_payload(unit, tmp_path)
    labels = {"compute.niuu.io/allocation": payload["allocation_id"]}
    existing = {"niuu-skuld": {"labels": labels, "running": True}}

    completed = docker.run(
        start_program(tmp_path), payload=payload, FAKE_CONTAINERS=json.dumps(existing)
    )

    assert completed.returncode == 0, completed.stderr
    commands = docker.commands()
    assert [c[2] for c in commands if c[:2] == ["docker", "restart"]] == ["niuu-skuld"]
    runs = [c for c in commands if c[:2] == ["docker", "run"]]
    assert [r[r.index("--name") + 1] for r in runs] == ["niuu-skuld-m8082", "niuu-skuld-m8083"]


async def test_one_member_started_again_leaves_the_others_alone(unit, docker, tmp_path):
    stage(unit)
    coder = unit.members[0].session.id
    await unit.runtime.restart_member(unit.lease, unit.bootstrap, coder)
    payload = json.loads(payloads(unit.runtime)[-1])
    payload["directories"] = []
    root = tmp_path / "vm"
    root.mkdir()
    (root / ".allocation").write_text(payload["allocation_id"])
    labels = {"compute.niuu.io/allocation": payload["allocation_id"]}
    existing = {
        name: {"labels": labels, "running": True}
        for name in ("niuu-skuld", "niuu-skuld-m8082", "niuu-skuld-m8083")
    }

    completed = docker.run(
        start_program(tmp_path), payload=payload, FAKE_CONTAINERS=json.dumps(existing)
    )

    assert completed.returncode == 0, completed.stderr
    restarts = [c for c in docker.commands() if c[:2] == ["docker", "restart"]]
    assert [c[2] for c in restarts] == ["niuu-skuld-m8082"]


def test_the_stop_program_stops_the_members_before_the_session_and_its_sidecars(docker, tmp_path):
    root = tmp_path / "vm"
    root.mkdir()
    (root / ".allocation").write_text("alloc")
    script = module._STOP.replace("'/var/lib/niuu/session'", repr(str(root)))
    labels = {"compute.niuu.io/allocation": "alloc"}
    containers = {
        name: {"labels": labels, "running": True}
        for name in ("niuu-skuld", "niuu-skuld-m8082", "niuu-skuld-m8083")
    }

    completed = docker.run(
        script,
        "alloc",
        "7",
        "null",
        json.dumps(["niuu-skuld-m8083", "niuu-skuld-m8082"]),
        FAKE_CONTAINERS=json.dumps(containers),
        FAKE_PS="side",
    )

    assert completed.returncode == 0, completed.stderr
    stops = [c[4] for c in docker.commands() if c[:2] == ["docker", "stop"]]
    assert stops == ["niuu-skuld-m8083", "niuu-skuld-m8082", "niuu-skuld", "side"]


def test_the_stop_program_skips_a_member_that_never_started_and_refuses_a_stranger(
    docker, tmp_path
):
    root = tmp_path / "vm"
    root.mkdir()
    (root / ".allocation").write_text("alloc")
    script = module._STOP.replace("'/var/lib/niuu/session'", repr(str(root)))
    mine = {"compute.niuu.io/allocation": "alloc"}

    started = docker.run(
        script,
        "alloc",
        "7",
        "null",
        json.dumps(["niuu-skuld-m8082"]),
        FAKE_CONTAINERS=json.dumps({"niuu-skuld": {"labels": mine, "running": True}}),
    )
    assert started.returncode == 0, started.stderr

    other = {"compute.niuu.io/allocation": "someone-else"}
    refused = docker.run(
        script,
        "alloc",
        "7",
        "null",
        json.dumps(["niuu-skuld-m8082"]),
        FAKE_CONTAINERS=json.dumps({"niuu-skuld-m8082": {"labels": other, "running": True}}),
    )
    assert b"Container ownership mismatch" in refused.stderr


@pytest.mark.parametrize(("running", "code"), [(True, 0), (False, 44)])
def test_the_member_program_reports_whether_a_container_runs(docker, running, code):
    labels = ["compute.niuu.io/allocation=alloc"]
    containers = {
        "niuu-skuld-m8082": {"labels": {"compute.niuu.io/allocation": "alloc"}, "running": running}
    }

    result = docker.run(
        module._MEMBER,
        "running",
        "niuu-skuld-m8082",
        "7",
        json.dumps(labels),
        FAKE_CONTAINERS=json.dumps(containers),
    )

    assert result.returncode == code


def test_the_member_program_finds_no_container_stopped_and_refuses_a_stranger(docker):
    labels = json.dumps(["compute.niuu.io/allocation=alloc"])

    absent = docker.run(module._MEMBER, "running", "niuu-skuld-m8082", "7", labels)
    other = {
        "niuu-skuld-m8082": {"labels": {"compute.niuu.io/allocation": "other"}, "running": True}
    }
    stranger = docker.run(
        module._MEMBER, "stop", "niuu-skuld-m8082", "7", labels, FAKE_CONTAINERS=json.dumps(other)
    )

    assert absent.returncode == 44
    assert b"Container ownership mismatch" in stranger.stderr
    assert not [c for c in docker.commands() if c[:2] == ["docker", "stop"]]


def test_the_member_program_stops_a_running_member(docker):
    labels = json.dumps(["compute.niuu.io/allocation=alloc"])
    containers = {
        "niuu-skuld-m8082": {"labels": {"compute.niuu.io/allocation": "alloc"}, "running": True}
    }

    result = docker.run(
        module._MEMBER,
        "stop",
        "niuu-skuld-m8082",
        "7",
        labels,
        FAKE_CONTAINERS=json.dumps(containers),
    )

    assert result.returncode == 0, result.stderr
    assert ["docker", "stop", "--time", "7", "niuu-skuld-m8082"] in docker.commands()
