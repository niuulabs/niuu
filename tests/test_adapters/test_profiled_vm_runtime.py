from uuid import uuid4

import pytest

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.profiled_vm_runtime import ProfiledVmRuntime
from volundr.domain.compute import ComputeLease, ComputeSlot, MachineBootstrap, SlotLease
from volundr.domain.compute_hosts import HostPlatform
from volundr.domain.models import PodSpecAdditions, Session, SessionSpec
from volundr.domain.vm_runtime import VmRuntime


class RecordingRuntime(VmRuntime):
    instances = []

    def __init__(self, *, name):
        self.name = name
        self.calls = []
        self.instances.append(self)

    def machine_bootstrap(self, defaults):
        self.calls.append("machine_bootstrap")
        return defaults

    def configure_credentials(self, credential_store):
        self.calls.append(("configure_credentials", credential_store))

    def session_bootstrap(self, session, spec, machine):
        self.calls.append("session_bootstrap")
        return machine

    def bootstrap(self, session, spec, defaults):
        self.calls.append("bootstrap")
        return defaults

    async def warm(self, lease, bootstrap):
        self.calls.append("warm")

    async def prepare(self, lease, bootstrap):
        self.calls.append("prepare")

    async def start(self, lease, bootstrap):
        self.calls.append("start")

    async def target(self, lease, bootstrap):
        self.calls.append("target")
        return SessionProxyTarget(f"http://{self.name}", self.name, 1)

    async def ready(self, lease, bootstrap):
        self.calls.append("ready")
        return True

    async def stop(self, lease, bootstrap):
        self.calls.append("stop")

    async def close(self):
        self.calls.append("close")


@pytest.fixture(autouse=True)
def _clear_instances():
    RecordingRuntime.instances.clear()


async def test_routes_session_and_lease_operations_by_profile():
    direct = RecordingRuntime(name="docker")
    openshell = RecordingRuntime(name="openshell")
    runtime = ProfiledVmRuntime(default=direct, profiles={"openshell": openshell})
    session = Session(
        id=uuid4(),
        name="OpenShell",
        workload_config={"compute_profile": "openshell"},
    )
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
    machine = ComputeLease(
        id=uuid4(),
        pool_id="pool",
        profile="openshell",
        request_fingerprint="fingerprint",
    )
    lease = SlotLease(
        allocation=machine,
        binding=ComputeSlot(
            id=uuid4(),
            allocation_id=machine.id,
            slot=0,
            pool_id="pool",
            session_id=session.id,
            tenant_id="tenant",
            owner_id="owner",
        ),
    )
    bootstrap = MachineBootstrap()

    runtime.machine_bootstrap(bootstrap)
    runtime.session_bootstrap(session, spec, bootstrap)
    runtime.bootstrap(session, spec, bootstrap)
    await runtime.warm(machine, bootstrap)
    await runtime.prepare(lease, bootstrap)
    await runtime.start(lease, bootstrap)
    assert (await runtime.target(lease, bootstrap)).connect_host == "openshell"
    assert await runtime.ready(lease, bootstrap)
    await runtime.stop(lease, bootstrap)
    await runtime.close()

    assert direct.calls == ["machine_bootstrap", "close"]
    assert openshell.calls == [
        "session_bootstrap",
        "bootstrap",
        "warm",
        "prepare",
        "start",
        "target",
        "ready",
        "stop",
        "close",
    ]


def test_omitted_session_profile_and_machine_bootstrap_use_effective_default_profile():
    direct = RecordingRuntime(name="docker")
    openshell = RecordingRuntime(name="openshell")
    runtime = ProfiledVmRuntime(
        default=direct,
        profiles={"small": openshell},
        default_profile="small",
    )
    session = Session(id=uuid4(), name="Default profile")
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
    bootstrap = MachineBootstrap()

    runtime.machine_bootstrap(bootstrap)
    runtime.session_bootstrap(session, spec, bootstrap)
    runtime.bootstrap(session, spec, bootstrap)

    assert direct.calls == []
    assert openshell.calls == ["machine_bootstrap", "session_bootstrap", "bootstrap"]


def test_rejects_non_runtime_instance():
    with pytest.raises(TypeError, match="default must implement"):
        ProfiledVmRuntime(default=object())
    with pytest.raises(TypeError, match="profiles must implement"):
        ProfiledVmRuntime(default=RecordingRuntime(name="docker"), profiles={"bad": object()})


class ReportingRecordingRuntime(RecordingRuntime):
    def report_startup_to(self, report):
        self.calls.append(("report_startup_to", report))


def test_forwards_the_startup_reporter_to_every_runtime_that_reports():
    direct = ReportingRecordingRuntime(name="docker")
    openshell = ReportingRecordingRuntime(name="openshell")
    silent = RecordingRuntime(name="plain")
    runtime = ProfiledVmRuntime(default=direct, profiles={"openshell": openshell, "plain": silent})

    def report(session_id, key, label):
        return None

    runtime.report_startup_to(report)

    assert direct.calls == [("report_startup_to", report)]
    assert openshell.calls == [("report_startup_to", report)]
    assert silent.calls == []


def test_forwards_credential_store_to_profile_runtimes():
    runtime = ProfiledVmRuntime(
        default=RecordingRuntime(name="docker"),
        profiles={"openshell": RecordingRuntime(name="openshell")},
    )
    store = object()

    runtime.configure_credentials(store)

    assert [instance.calls for instance in RecordingRuntime.instances] == [
        [("configure_credentials", store)],
        [("configure_credentials", store)],
    ]


class RootDaemonRuntime(RecordingRuntime):
    @property
    def requires_root_daemon(self):
        return True


def test_a_runtime_needs_no_root_daemon_and_sets_no_slot_limits_unless_it_says_so():
    runtime = RecordingRuntime(name="host")

    assert runtime.requires_root_daemon is False
    assert runtime.slot_limits is None


@pytest.mark.parametrize(
    "default_needs,profile_needs,expected",
    [
        (False, False, False),
        (False, True, True),
        (True, False, True),
        (True, True, True),
    ],
)
def test_a_profiled_runtime_needs_a_root_daemon_when_any_of_its_runtimes_does(
    default_needs, profile_needs, expected
):
    default = (RootDaemonRuntime if default_needs else RecordingRuntime)(name="default")
    profiled = (RootDaemonRuntime if profile_needs else RecordingRuntime)(name="profiled")
    runtime = ProfiledVmRuntime(default=default, profiles={"other": profiled})

    assert runtime.requires_root_daemon is expected


def test_a_profiled_runtime_without_profiles_follows_its_default():
    assert ProfiledVmRuntime(default=RootDaemonRuntime(name="d"), profiles={}).requires_root_daemon
    assert not ProfiledVmRuntime(
        default=RecordingRuntime(name="d"), profiles={}
    ).requires_root_daemon


class MacRuntime(RecordingRuntime):
    @property
    def host_platforms(self):
        return frozenset(HostPlatform)


def test_a_runtime_runs_on_linux_hosts_unless_it_says_more():
    assert RecordingRuntime(name="host").host_platforms == {HostPlatform.LINUX}


def test_a_profiled_runtime_runs_only_where_all_of_its_runtimes_do():
    mac = ProfiledVmRuntime(default=MacRuntime(name="d"), profiles={"m": MacRuntime(name="m")})
    mixed = ProfiledVmRuntime(
        default=MacRuntime(name="d"), profiles={"l": RecordingRuntime(name="l")}
    )

    assert mac.host_platforms == {HostPlatform.LINUX, HostPlatform.DARWIN}
    assert mixed.host_platforms == {HostPlatform.LINUX}
