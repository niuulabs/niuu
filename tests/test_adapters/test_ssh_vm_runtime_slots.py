"""SshContainerVmRuntime with slot_isolation: each session in its own slot of the VM.

Guest programs run here for real against fake ``docker``, ``ip`` and ``chown``
binaries that record their arguments; the slot supervisor's own behaviour is in
``test_vm_slot_supervisor``.
"""

import io
import json
import logging
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from volundr.adapters.outbound import ssh_vm_runtime as module
from volundr.adapters.outbound import vm_slot_supervisor
from volundr.adapters.outbound.guest_credential_delivery import SshPushCredentialDelivery
from volundr.adapters.outbound.ssh_openshell_vm_runtime import SshOpenShellVmRuntime
from volundr.adapters.outbound.ssh_vm_runtime import SshContainerVmRuntime
from volundr.domain.compute import Machine, MachineBootstrap, MachineState
from volundr.domain.guest_access import GuestCommandResult
from volundr.domain.guest_credentials import GuestCredentialDelivery
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec
from volundr.domain.service_account_tokens import ServiceAccountTokenPort
from volundr.domain.vm_runtime import SlotLimits, VmRuntimeStageError

ISOLATION = {"cpu": 1.0, "memory_mib": 1024, "pids": 256}


class SlotView:
    """The lease view the slot ledger hands runtimes (``SlotLease``)."""

    def __init__(self, allocation, session_id, slot, machine):
        self.id = allocation
        self.session_id = session_id
        self.slot = slot
        self.machine = machine
        self.profile = "small"
        self.pool_id = "pool"
        self.owner_id = "owner"


def _keys(tmp_path: Path) -> tuple[Path, Path]:
    key = Ed25519PrivateKey.generate()
    private, public = tmp_path / "key", tmp_path / "key.pub"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
    )
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
    )
    return private, public


def _runtime(tmp_path: Path, **kwargs) -> SshContainerVmRuntime:
    private, public = _keys(tmp_path)
    return SshContainerVmRuntime(
        ssh_private_key_file=str(private),
        ssh_public_key_file=str(public),
        data_dir=str(tmp_path / "data"),
        skuld_image="test/skuld:dev",
        command_timeout_seconds=0.05,
        **kwargs,
    )


@pytest.fixture
def slots(tmp_path):
    runtime = _runtime(tmp_path, slot_isolation=ISOLATION)
    allocation = uuid4()
    machine = Machine(
        allocation_id=allocation,
        resource_id="vm",
        state=MachineState.RUNNING,
        addresses=("192.0.2.10",),
    )
    session = Session(name="test", owner_id="owner", tenant_id="tenant", source=GitSource())
    spec = SessionSpec(values={"session": {}}, pod_spec=PodSpecAdditions())
    bootstrap = runtime.bootstrap(session, spec, MachineBootstrap())
    lease = SlotView(allocation, session.id, 1, machine)
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(0, b"{}"))
    runtime._guest_access.open_tunnel = AsyncMock()
    runtime._guest_access.close_tunnel = AsyncMock()
    return runtime, bootstrap, lease


def _programs(runtime) -> list[str]:
    return [call.args[2] for call in runtime._guest_access.execute.await_args_list]


def _payloads(runtime) -> list:
    return [call.kwargs.get("data") for call in runtime._guest_access.execute.await_args_list]


async def test_start_creates_the_slot_then_places_the_session_in_it(slots):
    runtime, bootstrap, lease = slots
    steps = []
    runtime.report_startup_to(lambda sid, key, label: steps.append(key))

    await runtime.start(lease, bootstrap)

    programs, payloads = _programs(runtime), _payloads(runtime)
    supervisor = runtime._python(runtime._supervisor, "up")
    assert programs[0] == supervisor
    spec = json.loads(payloads[0])
    assert (spec["slot"], spec["uid"], spec["address"]) == (1, 61001, "10.249.0.6")
    assert spec["image"] == "test/skuld:dev" and spec["platform_port"] == 18080
    up = runtime._guest_access.execute.await_args_list[0].kwargs
    assert up["safe_error_prefix"] == vm_slot_supervisor.STAGE_PREFIX
    assert up["safe_detail_prefix"] == vm_slot_supervisor.DETAIL_PREFIX
    files = json.loads(payloads[1])
    assert [item["path"] for item in files] == ["/var/lib/niuu/slots/1/etc/session-launch.json"]
    key = f"{lease.id}-slot-1"
    assert programs[2] == runtime._python(
        module._PREPARE, key, "empty", '["home/.codex/tmp"]', "/var/lib/niuu/slots/1/session"
    )
    tunnel = runtime._guest_access.open_tunnel.await_args.kwargs
    assert tunnel["remote_host"] == "10.249.0.6" and tunnel["key"] == key
    assert (tunnel["reverse_bind_host"], tunnel["reverse_port"]) == ("127.0.0.1", 18101)
    launch = json.loads(payloads[3])
    assert launch["allocation_id"] == str(lease.id)
    assert launch["slot"]["skuld"] == "niuu-slot-1-skuld"
    assert launch["environment"]["SKULD__HOST"] == "0.0.0.0"
    assert launch["environment"]["SKULD__VOLUNDR_API_URL"] == "http://10.249.0.5:18080"
    assert launch["environment"]["SKULD__PORT"] == "8081"
    assert steps == ["guest_slot", "guest_files", "guest_tunnel", "guest_skuld"]


async def test_a_restored_slot_keeps_its_own_storage_key(slots):
    runtime, bootstrap, lease = slots
    archive = runtime._data / str(lease.session_id) / "session.tar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"archive")

    await runtime.start(lease, bootstrap)

    restore = runtime._guest_access.execute.await_args_list[2]
    assert restore.args[2] == runtime._python(
        module._PREPARE,
        f"{lease.id}-slot-1",
        "restore",
        '["home/.codex/tmp"]',
        "/var/lib/niuu/slots/1/session",
    )
    assert "stdin" in restore.kwargs


async def test_stop_archives_reports_limits_then_removes_the_slot(slots, caplog, monkeypatch):
    runtime, bootstrap, lease = slots
    counted = Mock()
    monkeypatch.setattr(module, "get_observability", lambda: SimpleNamespace(count=counted))
    limits = json.dumps({"memory.oom_kill": 2, "memory.max": 0, "pids.max": 1}).encode()

    async def execute(lease_, bootstrap_, command, **kwargs):
        if command == runtime._python(runtime._supervisor, "limits"):
            return GuestCommandResult(0, limits)
        return GuestCommandResult(0)

    runtime._guest_access.execute = AsyncMock(side_effect=execute)

    with caplog.at_level(logging.ERROR, logger=module.__name__):
        await runtime.stop(lease, bootstrap)

    programs = _programs(runtime)
    stop_spec = json.dumps(
        {
            "data_root": "/var/lib/niuu/slots/1/session",
            "marker": f"{lease.id}-slot-1",
            "skuld": "niuu-slot-1-skuld",
            "slot_label": "compute.niuu.io/slot=1",
        }
    )
    assert programs[0] == runtime._python(module._STOP, str(lease.id), "30", stop_spec)
    assert "-C /var/lib/niuu/slots/1/session" in programs[1]
    assert programs[2:] == [
        runtime._python(runtime._supervisor, "limits"),
        runtime._python(runtime._supervisor, "down"),
    ]
    runtime._guest_access.close_tunnel.assert_awaited_once_with(f"{lease.id}-slot-1")
    assert [call.args[1] for call in counted.call_args_list] == [2, 1]
    assert {call.kwargs["attributes"]["limit"] for call in counted.call_args_list} == {
        "memory.oom_kill",
        "pids.max",
    }
    assert "slot_isolation.memory_mib" in caplog.text and "slot_isolation.pids" in caplog.text


async def test_stop_before_restore_keeps_the_archive_but_still_removes_the_slot(slots):
    runtime, bootstrap, lease = slots
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(42, b"{}"))

    await runtime.stop(lease, bootstrap)

    programs = _programs(runtime)
    assert not any("tar" in program for program in programs)
    assert programs[-1] == runtime._python(runtime._supervisor, "down")


async def test_a_failed_teardown_fails_stop_and_keeps_the_tunnel_for_the_retry(slots):
    runtime, bootstrap, lease = slots

    async def execute(lease_, bootstrap_, command, **kwargs):
        if command == runtime._python(runtime._supervisor, "down"):
            raise VmRuntimeStageError("slot-down", "Slot 1 teardown left: user niuu-slot-1")
        return GuestCommandResult(0, b"{}")

    runtime._guest_access.execute = AsyncMock(side_effect=execute)

    with pytest.raises(VmRuntimeStageError, match="slot-down"):
        await runtime.stop(lease, bootstrap)
    runtime._guest_access.close_tunnel.assert_not_awaited()


async def test_credential_files_land_in_the_slots_directories(slots):
    runtime, bootstrap, lease = slots
    items = [{"directory": "/etc/niuu/forge-session", "name": "token", "content": "t"}]

    await runtime.write_files(lease, bootstrap, items)
    await runtime.file_exists(lease, bootstrap, "/etc/niuu/forge-session/token")

    written = json.loads(_payloads(runtime)[0])
    assert written == [
        {"directory": "/var/lib/niuu/slots/1/etc/forge-session", "name": "token", "content": "t"}
    ]
    assert items[0]["directory"] == "/etc/niuu/forge-session"
    assert "/var/lib/niuu/slots/1/etc/forge-session/token" in _programs(runtime)[1]


class _AllocationDelivery(GuestCredentialDelivery):
    async def start(self, lease, bootstrap, plan, launch, files):
        """Nothing to deliver in this test."""


def _with_token_volume(bootstrap: MachineBootstrap) -> MachineBootstrap:
    files = tuple(
        f.model_copy(
            update={"content": json.dumps({**json.loads(f.content), "forge_session": True})}
        )
        if f.path == module._LAUNCH
        else f
        for f in bootstrap.files
    )
    return bootstrap.model_copy(update={"files": files})


async def test_an_allocation_scoped_delivery_cannot_serve_a_slot_past_zero(slots):
    runtime, bootstrap, lease = slots
    runtime.configure_credential_delivery(_AllocationDelivery())
    bootstrap = _with_token_volume(bootstrap)

    with pytest.raises(ValueError, match="vouches for the whole allocation"):
        await runtime.start(lease, bootstrap)
    runtime._guest_access.execute.assert_not_awaited()

    lease.slot = 0
    await runtime.start(lease, bootstrap)
    assert _programs(runtime)[0] == runtime._python(runtime._supervisor, "up")


async def test_a_per_slot_delivery_serves_every_slot(slots):
    runtime, bootstrap, lease = slots
    delivery = AsyncMock(spec=SshPushCredentialDelivery)
    delivery.serves_slots = True
    runtime.configure_credential_delivery(delivery)

    await runtime.start(lease, _with_token_volume(bootstrap))

    delivery.start.assert_awaited_once()


async def test_without_slot_isolation_only_slot_zero_exists(tmp_path):
    runtime = _runtime(tmp_path)
    lease = SlotView(uuid4(), uuid4(), 2, None)

    for call in (runtime.start, runtime.stop, runtime.target):
        with pytest.raises(ValueError, match="slot_isolation"):
            await call(lease, MachineBootstrap())
    with pytest.raises(ValueError, match="slot_isolation"):
        await runtime.write_files(lease, MachineBootstrap(), [])


async def test_slot_zero_without_isolation_keeps_the_whole_vm_layout(tmp_path):
    runtime = _runtime(tmp_path)
    runtime._guest_access.open_tunnel = AsyncMock()
    lease = SlotView(uuid4(), uuid4(), 0, None)

    await runtime.target(lease, MachineBootstrap())

    tunnel = runtime._guest_access.open_tunnel.await_args.kwargs
    assert "key" not in tunnel and "remote_host" not in tunnel
    assert tunnel["reverse_port"] == 18080
    assert runtime._layout(lease) is None and runtime._supervisor == ""


def test_runtimes_with_their_own_sandbox_refuse_slot_isolation(tmp_path):
    with pytest.raises(ValueError, match="own sandbox boundary"):
        SshOpenShellVmRuntime(
            ssh_private_key_file=str(_keys(tmp_path)[0]),
            ssh_public_key_file=str(_keys(tmp_path)[1]),
            data_dir=str(tmp_path / "data"),
            skuld_image="test/skuld:dev",
            slot_isolation=ISOLATION,
        )


def test_slot_isolation_settings_are_validated_at_composition(tmp_path):
    with pytest.raises(ValueError, match="memory_mib"):
        _runtime(tmp_path, slot_isolation={"cpu": 1, "pids": 1})


def test_slot_capacity_failures_name_the_knob(tmp_path):
    runtime = _runtime(tmp_path, slot_isolation={**ISOLATION, "subnet": "10.249.0.0/29"})

    with pytest.raises(ValueError, match="larger subnet"):
        runtime._layout(SlotView(uuid4(), uuid4(), 2, None))


def test_only_deliveries_that_key_by_slot_serve_slots():
    delivery = SshPushCredentialDelivery(service_account_tokens=Mock(spec=ServiceAccountTokenPort))

    assert delivery.serves_slots
    assert not _AllocationDelivery().serves_slots


async def test_ssh_push_refreshers_are_keyed_by_slot(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from volundr.domain.guest_credentials import GuestCredentialPlan

    tokens = AsyncMock(spec=ServiceAccountTokenPort)
    tokens.issue.return_value = SimpleNamespace(
        token="t", expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    delivery = SshPushCredentialDelivery(service_account_tokens=tokens)
    plan = GuestCredentialPlan.from_launch(
        {
            "service_account": "sa",
            "token_volumes": [
                {
                    "volume": "v",
                    "directory": "/run/niuu/session/tokens/v",
                    "tokens": [{"path": "token", "audience": "a", "expiration_seconds": 3600}],
                }
            ],
        }
    )
    files = AsyncMock()
    allocation = uuid4()
    zero, one = (SlotView(allocation, uuid4(), n, None) for n in (0, 1))

    await delivery.resume(zero, MachineBootstrap(), plan, files)
    await delivery.resume(one, MachineBootstrap(), plan, files)

    assert set(delivery._refreshers) == {str(allocation), f"{allocation}-slot-1"}
    await delivery.stop(one, MachineBootstrap())
    assert set(delivery._refreshers) == {str(allocation)}
    await delivery.close()


async def test_guest_access_forwards_a_slots_tunnel_to_its_address(tmp_path, monkeypatch):
    from volundr.adapters.outbound import ssh_guest_access

    runtime = _runtime(tmp_path, slot_isolation=ISOLATION)
    process = AsyncMock()
    process.returncode = None
    process.terminate = Mock()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(ssh_guest_access.asyncio, "create_subprocess_exec", spawn)
    allocation = uuid4()
    machine = Machine(
        allocation_id=allocation,
        resource_id="vm",
        state=MachineState.RUNNING,
        addresses=("192.0.2.10",),
    )
    session = Session(name="t", owner_id="o", tenant_id="t", source=GitSource())
    bootstrap = runtime.bootstrap(
        session, SessionSpec(values={}, pod_spec=PodSpecAdditions()), MachineBootstrap()
    )
    lease = SlotView(allocation, session.id, 1, machine)

    target = await runtime.target(lease, bootstrap)

    args = spawn.call_args.args
    assert f"127.0.0.1:{target.connect_port}:10.249.0.6:8081" in args
    assert "127.0.0.1:18101:127.0.0.1:8080" in args
    assert f"{allocation}-slot-1" in runtime._guest_access._tunnels
    await runtime.close()


# -- the guest programs, run against fake docker/ip/chown ----------------------

_FAKE = r"""
import json, os, sys
name = os.path.basename(sys.argv[0])
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps([name, *sys.argv[1:]]) + '\n')
if name == 'docker' and sys.argv[1:3] == ['container', 'inspect']:
    containers = json.loads(os.environ.get('FAKE_CONTAINERS', '{}'))
    if sys.argv[3] not in containers:
        sys.exit(1)
    print(json.dumps([{'Config': {'Labels': containers[sys.argv[3]]}}]))
if name == 'docker' and sys.argv[1:3] == ['ps', '-aq']:
    print(os.environ.get('FAKE_PS', ''))
"""


@pytest.fixture
def fakes(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker", "ip", "chown"):
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\n{_FAKE}")
        path.chmod(0o755)
    log = tmp_path / "commands.jsonl"

    def run(
        program: str, *args: str, payload: dict | None = None, **env
    ) -> subprocess.CompletedProcess:
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
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return SimpleNamespace(run=run, commands=commands)


async def _launch(slots, tmp_path, *, metadata: bool = True) -> dict:
    runtime, bootstrap, lease = slots
    await runtime.start(lease, bootstrap)
    payload = json.loads(_payloads(runtime)[-1])
    payload["slot"]["data_root"] = str(tmp_path / "slot-data")
    payload["sidecars"] = [
        {
            "name": "agent",
            "image": "agent:1",
            "command": [],
            "args": [],
            "environment": {},
            "user": "0",
            "mounts": [],
        }
    ]
    if metadata:
        payload["metadata_address"] = "169.254.169.254"
    root = Path(payload["slot"]["data_root"])
    root.mkdir()
    (root / ".allocation").write_text(payload["slot"]["marker"])
    return payload


async def test_start_program_runs_every_container_in_the_slot(slots, tmp_path, fakes):
    payload = await _launch(slots, tmp_path)

    completed = fakes.run(module._START, payload=payload)

    assert completed.returncode == 0, completed.stderr
    commands = fakes.commands()
    slot = payload["slot"]
    assert [
        "ip",
        "netns",
        "exec",
        "niuu-slot-1",
        "ip",
        "addr",
        "add",
        "169.254.169.254/32",
        "dev",
        "lo",
    ] in commands
    assert ["chown", "-R", "-h", "61001:61001", slot["data_root"]] in commands
    placement = [
        "--network",
        "container:niuu-slot-1-holder",
        "--pid",
        "container:niuu-slot-1-holder",
        "--cgroup-parent",
        "niuu_slot_1.slice",
        "--cap-drop",
        "NET_RAW",
    ]
    runs = [c for c in commands if c[:2] == ["docker", "run"]]
    sidecar, skuld = runs
    assert sidecar[sidecar.index("--name") + 1] == "niuu-slot-1-sidecar-agent"
    for started in (sidecar, skuld):
        assert "--init" not in started
        assert started[started.index("--network") :][: len(placement)] == placement
        assert "compute.niuu.io/slot=1" in started
    assert skuld[skuld.index("--name") + 1] == "niuu-slot-1-skuld"
    assert skuld[skuld.index("--user") + 1] == "61001:61001"
    assert f"type=bind,src={slot['identity']}/passwd,dst=/etc/passwd,readonly" in skuld


async def test_start_program_refuses_another_slots_storage_and_containers(slots, tmp_path, fakes):
    payload = await _launch(slots, tmp_path, metadata=False)
    root = Path(payload["slot"]["data_root"])
    (root / ".allocation").write_text(payload["allocation_id"])

    assert b"Workspace allocation mismatch" in fakes.run(module._START, payload=payload).stderr

    (root / ".allocation").write_text(payload["slot"]["marker"])
    other = {
        "niuu-slot-1-skuld": {
            "compute.niuu.io/allocation": payload["allocation_id"],
            "compute.niuu.io/slot": "2",
        }
    }
    refused = fakes.run(module._START, payload=payload, FAKE_CONTAINERS=json.dumps(other))
    assert b"Container ownership mismatch" in refused.stderr


async def test_start_program_without_a_slot_keeps_the_whole_vm_layout(tmp_path, fakes):
    payload = {
        "allocation_id": "a",
        "image": "skuld",
        "environment": {},
        "repo": "",
        "branch": "",
        "sidecars": [
            {"name": "s", "image": "s", "command": [], "args": [], "environment": {}, "mounts": []}
        ],
    }
    script = module._START.replace("'/var/lib/niuu/session'", repr(str(tmp_path / "vm")))
    (tmp_path / "vm").mkdir()
    (tmp_path / "vm" / ".allocation").write_text("a")

    completed = fakes.run(script, payload=payload)

    assert completed.returncode == 0, completed.stderr
    sidecar, skuld = [c for c in fakes.commands() if c[:2] == ["docker", "run"]]
    assert sidecar[:9] == [
        "docker",
        "run",
        "--detach",
        "--init",
        "--restart",
        "unless-stopped",
        "--name",
        "niuu-sidecar-s",
        "--network",
    ]
    assert skuld[:8] == [
        "docker",
        "run",
        "--detach",
        "--init",
        "--name",
        "niuu-skuld",
        "--network",
        "host",
    ]
    assert "--user" not in skuld
    assert ["chown", "-R", "-h", "1000:1000", str(tmp_path / "vm")] in fakes.commands()


def test_stop_program_stops_only_its_slots_containers(tmp_path, fakes):
    root = tmp_path / "slot"
    root.mkdir()
    (root / ".allocation").write_text("alloc-slot-2")
    spec = json.dumps(
        {
            "data_root": str(root),
            "marker": "alloc-slot-2",
            "skuld": "niuu-slot-2-skuld",
            "slot_label": "compute.niuu.io/slot=2",
        }
    )
    labels = {
        "niuu-slot-2-skuld": {"compute.niuu.io/allocation": "alloc", "compute.niuu.io/slot": "2"}
    }

    completed = fakes.run(
        module._STOP, "alloc", "7", spec, FAKE_CONTAINERS=json.dumps(labels), FAKE_PS="side"
    )

    assert completed.returncode == 0, completed.stderr
    commands = fakes.commands()
    assert ["docker", "stop", "--time", "7", "niuu-slot-2-skuld"] in commands
    assert [
        "docker",
        "ps",
        "-aq",
        "--filter",
        "label=compute.niuu.io/allocation=alloc",
        "--filter",
        "label=compute.niuu.io/slot=2",
    ] in commands
    assert ["docker", "stop", "--time", "7", "side"] in commands

    wrong = {
        "niuu-slot-2-skuld": {"compute.niuu.io/allocation": "alloc", "compute.niuu.io/slot": "3"}
    }
    refused = fakes.run(module._STOP, "alloc", "7", spec, FAKE_CONTAINERS=json.dumps(wrong))
    assert b"Container ownership mismatch" in refused.stderr

    (root / ".allocation").unlink()
    assert fakes.run(module._STOP, "alloc", "7", spec).returncode == 42


def test_prepare_program_restores_into_the_slot_root(tmp_path):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        member = tarfile.TarInfo("./home/kept")
        member.size = 4
        tar.addfile(member, io.BytesIO(b"kept"))
    root = tmp_path / "slots" / "1" / "session"
    root.mkdir(parents=True)

    restored = subprocess.run(
        [sys.executable, "-c", module._PREPARE, "alloc-slot-1", "restore", "[]", str(root)],
        input=archive.getvalue(),
        capture_output=True,
    )

    assert restored.returncode == 0, restored.stderr
    assert (root / "home" / "kept").read_bytes() == b"kept"
    assert (root / ".allocation").read_text() == "alloc-slot-1"


def test_the_container_runtime_runs_under_the_guests_root_docker(tmp_path):
    assert _runtime(tmp_path).requires_root_daemon is True
    assert _runtime(tmp_path, slot_isolation=ISOLATION).requires_root_daemon is True


def test_without_slot_isolation_the_runtime_reports_no_slot_limits(tmp_path):
    assert _runtime(tmp_path).slot_limits is None


def test_slot_limits_carry_the_isolation_settings(tmp_path):
    runtime = _runtime(tmp_path, slot_isolation={**ISOLATION, "uid_base": 62000})

    assert runtime.slot_limits == SlotLimits(uid_base=62000, cpu=1.0, memory_mib=1024, pids=256)


def test_slot_limits_keep_a_limit_the_operator_left_unset_as_none(tmp_path):
    runtime = _runtime(tmp_path, slot_isolation={"cpu": None, "memory_mib": 512, "pids": None})

    assert runtime.slot_limits == SlotLimits(uid_base=61000, cpu=None, memory_mib=512, pids=None)
