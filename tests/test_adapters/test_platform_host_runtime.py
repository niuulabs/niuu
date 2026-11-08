"""PlatformHostRuntime: one runtime per platform, routed by the claimed host's platform."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.platform_host_runtime import PlatformHostRuntime
from volundr.domain.compute import (
    PLATFORM_BOOTSTRAP_ROOT,
    BootstrapFile,
    ComputeLease,
    ComputeSlot,
    HostPlatform,
    Machine,
    MachineBootstrap,
    MachineState,
    SlotLease,
    platform_bootstrap,
)
from volundr.domain.models import PodSpecAdditions, Session, SessionSpec
from volundr.domain.session_runtimes import SessionGrant, SessionRuntimeKind
from volundr.domain.vm_runtime import SlotLimits, VmRuntime

LIMITS = SlotLimits(uid_base=100000, cpu=2.0, memory_mib=4096, pids=512)


class FakeRuntime(VmRuntime):
    """A one-platform runtime that records what it was handed."""

    def __init__(
        self,
        platform: HostPlatform,
        *,
        slots: bool = False,
        limits: SlotLimits | None = None,
        reuse: bool = False,
        root_daemon: bool = False,
        kind: SessionRuntimeKind | None = SessionRuntimeKind.HOST,
        grants: tuple[SessionGrant, ...] = (),
        refuse: str | None = None,
        platforms: frozenset[HostPlatform] | None = None,
        forge_token: bool = True,
    ) -> None:
        self._platform = platform
        self._platforms = platforms if platforms is not None else frozenset({platform})
        self._slots = slots
        self._limits = limits
        self._reuse = reuse
        self._root = root_daemon
        self._kind = kind
        self._grants = grants
        self._refuse = refuse
        self._forge_token = forge_token
        self.calls: list[tuple] = []

    @property
    def host_platforms(self):
        return self._platforms

    @property
    def supports_slots(self):
        return self._slots

    @property
    def slot_limits(self):
        return self._limits

    @property
    def supports_reuse(self):
        return self._reuse

    @property
    def requires_root_daemon(self):
        return self._root

    @property
    def session_runtime(self):
        return self._kind

    @property
    def session_grants(self):
        return self._grants

    @property
    def delivers_forge_session_token(self):
        return self._forge_token

    def _built(self, defaults: MachineBootstrap) -> MachineBootstrap:
        if self._refuse is not None:
            raise ValueError(self._refuse)
        return MachineBootstrap(
            files=(
                *defaults.files,
                BootstrapFile(path="/etc/launch", content=f"launch-{self._platform}"),
            ),
            commands=(("true",),),
            ssh_authorized_keys=("ssh-ed25519 AAAA",),
        )

    def machine_bootstrap(self, defaults):
        self.calls.append(("machine_bootstrap", defaults))
        return self._built(defaults)

    def session_bootstrap(self, session, spec, machine):
        self.calls.append(("session_bootstrap", machine))
        return self._built(machine)

    def bootstrap(self, session, spec, defaults):
        self.calls.append(("bootstrap", defaults))
        return self._built(defaults)

    async def warm(self, lease, bootstrap):
        self.calls.append(("warm", bootstrap))

    async def prepare(self, lease, bootstrap):
        self.calls.append(("prepare", bootstrap))

    async def start(self, lease, bootstrap):
        self.calls.append(("start", bootstrap))

    async def target(self, lease, bootstrap):
        self.calls.append(("target", bootstrap))
        return SessionProxyTarget(f"http://{self._platform}", str(self._platform), 1)

    async def ready(self, lease, bootstrap):
        self.calls.append(("ready", bootstrap))
        return True

    async def stop(self, lease, bootstrap):
        self.calls.append(("stop", bootstrap))

    async def recycle(self, lease, bootstrap):
        self.calls.append(("recycle", bootstrap))

    async def close(self):
        self.calls.append(("close",))

    # collaborators
    def stage_forge_session(self, session_id, credential):
        self.calls.append(("stage", session_id, credential))

    def discard_forge_session(self, session_id):
        self.calls.append(("discard", session_id))

    def report_startup_to(self, report):
        self.calls.append(("report", report))

    def configure_credential_delivery(self, delivery):
        self.calls.append(("delivery", delivery))

    def configure_credentials(self, credential_store):
        self.calls.append(("credentials", credential_store))


class BareRuntime(VmRuntime):
    """A runtime with none of the optional collaborator hooks."""

    def __init__(self, platform: HostPlatform) -> None:
        self._platform = platform

    @property
    def host_platforms(self):
        return frozenset({self._platform})

    def machine_bootstrap(self, defaults):
        return defaults

    def session_bootstrap(self, session, spec, machine):
        return machine

    def bootstrap(self, session, spec, defaults):
        return defaults

    async def warm(self, lease, bootstrap): ...
    async def prepare(self, lease, bootstrap): ...
    async def start(self, lease, bootstrap): ...

    async def target(self, lease, bootstrap):
        raise NotImplementedError

    async def ready(self, lease, bootstrap):
        return True

    async def stop(self, lease, bootstrap): ...
    async def close(self): ...


LINUX = HostPlatform.LINUX
DARWIN = HostPlatform.DARWIN


def _both(**linux):
    return FakeRuntime(LINUX, **linux), FakeRuntime(DARWIN)


def _runtime(linux=None, darwin=None):
    platforms = {}
    if linux is not None:
        platforms["linux"] = linux
    if darwin is not None:
        platforms["darwin"] = darwin
    return PlatformHostRuntime(platforms=platforms)


def _lease(platform: HostPlatform | None, *, with_machine: bool = True) -> SlotLease:
    allocation_id = uuid4()
    machine = None
    if with_machine:
        machine = Machine(
            allocation_id=allocation_id,
            resource_id="host-1",
            state=MachineState.RUNNING,
            platform=platform,
        )
    allocation = ComputeLease(
        id=allocation_id,
        pool_id="pool",
        profile="hosts",
        request_fingerprint="fp",
        machine=machine,
    )
    return SlotLease(
        allocation=allocation,
        binding=ComputeSlot(
            id=uuid4(),
            allocation_id=allocation_id,
            slot=0,
            pool_id="pool",
            session_id=uuid4(),
            tenant_id="tenant",
            owner_id="owner",
        ),
    )


def _session():
    return Session(id=uuid4(), name="s"), SessionSpec(values={}, pod_spec=PodSpecAdditions())


# ── construction ────────────────────────────────────────────────────────────


def test_needs_at_least_one_platform():
    with pytest.raises(ValueError, match="at least one platform"):
        PlatformHostRuntime(platforms={})


def test_unknown_platform_is_refused_with_the_known_ones():
    with pytest.raises(ValueError, match="Unknown host platform 'windows'.*darwin, linux"):
        PlatformHostRuntime(platforms={"windows": FakeRuntime(LINUX)})


def test_child_must_be_a_vm_runtime():
    with pytest.raises(TypeError, match="must implement VmRuntime"):
        PlatformHostRuntime(platforms={"linux": object()})  # type: ignore[dict-item]


def test_child_must_run_on_exactly_its_own_platform():
    with pytest.raises(ValueError, match="FakeRuntime runs sessions on darwin.*linux runtime"):
        PlatformHostRuntime(platforms={"linux": FakeRuntime(DARWIN)})


def test_child_with_several_platforms_is_refused():
    both = FakeRuntime(LINUX, platforms=frozenset({LINUX, DARWIN}))
    with pytest.raises(ValueError, match="darwin, linux"):
        PlatformHostRuntime(platforms={"linux": both})


def test_child_with_no_platform_is_refused():
    none = FakeRuntime(LINUX, platforms=frozenset())
    with pytest.raises(ValueError, match="no platform"):
        PlatformHostRuntime(platforms={"linux": none})


def test_children_must_run_sessions_the_same_way():
    with pytest.raises(ValueError, match="run sessions the same way"):
        _runtime(
            FakeRuntime(LINUX, kind=SessionRuntimeKind.HOST),
            FakeRuntime(DARWIN, kind=SessionRuntimeKind.CONTAINER),
        )


def test_children_must_offer_the_same_grants():
    with pytest.raises(ValueError, match="same session grants"):
        _runtime(
            FakeRuntime(LINUX, grants=(SessionGrant.SUDO,)),
            FakeRuntime(DARWIN),
        )


def test_slot_separating_children_must_share_limits():
    other = SlotLimits(uid_base=1, cpu=None, memory_mib=None, pids=None)
    with pytest.raises(ValueError, match="same slot limits"):
        _runtime(
            FakeRuntime(LINUX, slots=True, limits=LIMITS),
            FakeRuntime(DARWIN, slots=True, limits=other),
        )


def test_slot_limits_may_differ_when_only_one_child_separates_slots():
    runtime = _runtime(
        FakeRuntime(LINUX, slots=True, limits=LIMITS),
        FakeRuntime(DARWIN, slots=False, limits=None),
    )
    assert runtime.slot_limits == LIMITS


# ── aggregated properties ───────────────────────────────────────────────────


def test_properties_aggregate_the_children():
    linux = FakeRuntime(
        LINUX,
        slots=True,
        limits=LIMITS,
        reuse=True,
        root_daemon=True,
        grants=(SessionGrant.SUDO,),
    )
    darwin = FakeRuntime(DARWIN, reuse=False, grants=(SessionGrant.SUDO,))
    runtime = _runtime(linux, darwin)

    assert runtime.host_platforms == frozenset({LINUX, DARWIN})
    assert runtime.requires_root_daemon is True
    assert runtime.supports_slots is True
    assert runtime.supports_reuse is False
    assert runtime.slot_limits == LIMITS
    assert runtime.session_runtime == SessionRuntimeKind.HOST
    assert runtime.session_grants == (SessionGrant.SUDO,)
    assert runtime.delivers_forge_session_token is True


def test_properties_when_no_child_separates_slots_or_needs_a_daemon():
    runtime = _runtime(FakeRuntime(LINUX, reuse=True), FakeRuntime(DARWIN, reuse=True))

    assert runtime.requires_root_daemon is False
    assert runtime.supports_slots is False
    assert runtime.slot_limits is None
    assert runtime.supports_reuse is True


def test_single_platform_runtime_reports_only_that_platform():
    runtime = _runtime(linux=FakeRuntime(LINUX, slots=True, limits=LIMITS))

    assert runtime.host_platforms == frozenset({LINUX})


def test_forge_session_token_delivery_needs_every_child_to_deliver():
    runtime = _runtime(FakeRuntime(LINUX), FakeRuntime(DARWIN, forge_token=False))
    assert runtime.delivers_forge_session_token is False

    bare = _runtime(BareRuntime(LINUX))
    assert bare.delivers_forge_session_token is False


def test_for_platform_returns_the_child_or_raises():
    linux = FakeRuntime(LINUX)
    runtime = _runtime(linux=linux)

    assert runtime.for_platform(LINUX) is linux
    with pytest.raises(ValueError, match="runs no sessions on darwin hosts"):
        runtime.for_platform(DARWIN)


def test_default_for_platform_is_the_runtime_itself():
    linux = FakeRuntime(LINUX)

    assert linux.for_platform(LINUX) is linux
    with pytest.raises(ValueError, match="FakeRuntime runs no sessions on darwin hosts"):
        linux.for_platform(DARWIN)


# ── collaborators ───────────────────────────────────────────────────────────


def test_collaborators_reach_every_child():
    linux, darwin = _both()
    runtime = _runtime(linux, darwin)
    session_id = uuid4()
    credential = object()
    report = lambda *args: None  # noqa: E731
    delivery = object()
    store = object()

    runtime.stage_forge_session(session_id, credential)  # type: ignore[arg-type]
    runtime.discard_forge_session(session_id)
    runtime.report_startup_to(report)
    runtime.configure_credential_delivery(delivery)  # type: ignore[arg-type]
    runtime.configure_credentials(store)  # type: ignore[arg-type]

    for child in (linux, darwin):
        assert child.calls == [
            ("stage", session_id, credential),
            ("discard", session_id),
            ("report", report),
            ("delivery", delivery),
            ("credentials", store),
        ]


def test_children_without_collaborator_hooks_are_skipped_except_credential_delivery():
    runtime = _runtime(BareRuntime(LINUX))

    runtime.stage_forge_session(uuid4(), object())  # type: ignore[arg-type]
    runtime.discard_forge_session(uuid4())
    runtime.report_startup_to(lambda *args: None)
    runtime.configure_credentials(object())  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="BareRuntime cannot deliver guest credentials"):
        runtime.configure_credential_delivery(object())  # type: ignore[arg-type]


async def test_close_closes_every_child():
    linux, darwin = _both()
    runtime = _runtime(linux, darwin)

    await runtime.close()

    assert linux.calls == [("close",)]
    assert darwin.calls == [("close",)]


# ── bootstraps ──────────────────────────────────────────────────────────────


def _launches(bootstrap: MachineBootstrap) -> dict[HostPlatform, str]:
    found = {}
    for platform in (LINUX, DARWIN):
        view = platform_bootstrap(bootstrap, platform)
        launch = [item.content for item in view.files if item.path == "/etc/launch"]
        if launch:
            found[platform] = launch[0]
    return found


def test_machine_bootstrap_is_built_for_every_platform():
    linux, darwin = _both()
    runtime = _runtime(linux, darwin)
    defaults = MachineBootstrap(files=(BootstrapFile(path="/etc/base", content="b"),))

    built = runtime.machine_bootstrap(defaults)

    assert _launches(built) == {LINUX: "launch-linux", DARWIN: "launch-darwin"}
    assert all(item.path.startswith(PLATFORM_BOOTSTRAP_ROOT) for item in built.files)
    assert built.commands == (("true",),)
    assert built.ssh_authorized_keys == ("ssh-ed25519 AAAA",)
    assert linux.calls == [("machine_bootstrap", defaults)]
    assert darwin.calls == [("machine_bootstrap", defaults)]


def test_bootstrap_builds_each_platform_from_the_defaults():
    linux, darwin = _both()
    runtime = _runtime(linux, darwin)
    session, spec = _session()

    built = runtime.bootstrap(session, spec, MachineBootstrap())

    assert _launches(built) == {LINUX: "launch-linux", DARWIN: "launch-darwin"}


def test_session_bootstrap_hands_each_child_only_its_own_machine_bootstrap():
    linux, darwin = _both()
    runtime = _runtime(linux, darwin)
    session, spec = _session()
    machine = runtime.machine_bootstrap(MachineBootstrap())
    linux.calls.clear()
    darwin.calls.clear()

    built = runtime.session_bootstrap(session, spec, machine)

    assert _launches(built) == {LINUX: "launch-linux", DARWIN: "launch-darwin"}
    assert [item.content for item in linux.calls[0][1].files] == ["launch-linux"]
    assert [item.content for item in darwin.calls[0][1].files] == ["launch-darwin"]


def test_a_refusing_platform_is_recorded_and_the_others_still_build(caplog):
    linux = FakeRuntime(LINUX)
    darwin = FakeRuntime(DARWIN, refuse="no container image on a Mac")
    runtime = _runtime(linux, darwin)

    with caplog.at_level(logging.INFO, logger="volundr.adapters.outbound.platform_host_runtime"):
        built = runtime.machine_bootstrap(MachineBootstrap())

    assert _launches(built) == {LINUX: "launch-linux"}
    assert "will not run on darwin hosts: no container image on a Mac" in caplog.text


def test_every_platform_refusing_raises_with_all_reasons():
    runtime = _runtime(
        FakeRuntime(LINUX, refuse="linux says no"),
        FakeRuntime(DARWIN, refuse="darwin says no"),
    )

    with pytest.raises(ValueError, match="No host platform can run this session") as error:
        runtime.machine_bootstrap(MachineBootstrap())

    assert "linux: linux says no" in str(error.value)
    assert "darwin: darwin says no" in str(error.value)


def test_other_errors_from_a_child_are_not_swallowed():
    class Broken(FakeRuntime):
        def machine_bootstrap(self, defaults):
            raise RuntimeError("boom")

    runtime = _runtime(Broken(LINUX), FakeRuntime(DARWIN))

    with pytest.raises(RuntimeError, match="boom"):
        runtime.machine_bootstrap(MachineBootstrap())


# ── routing ─────────────────────────────────────────────────────────────────


async def test_every_call_goes_to_the_hosts_platform_with_its_own_bootstrap():
    linux, darwin = _both()
    runtime = _runtime(linux, darwin)
    bootstrap = runtime.machine_bootstrap(MachineBootstrap())
    for child in (linux, darwin):
        child.calls.clear()
    lease = _lease(DARWIN)

    await runtime.warm(lease.allocation, bootstrap)
    await runtime.prepare(lease, bootstrap)
    await runtime.start(lease, bootstrap)
    target = await runtime.target(lease, bootstrap)
    assert await runtime.ready(lease, bootstrap)
    await runtime.stop(lease, bootstrap)
    await runtime.recycle(lease, bootstrap)

    assert linux.calls == []
    assert target.connect_host == "darwin"
    assert [call[0] for call in darwin.calls] == [
        "warm",
        "prepare",
        "start",
        "target",
        "ready",
        "stop",
        "recycle",
    ]
    for call in darwin.calls:
        assert [item.content for item in call[1].files] == ["launch-darwin"]
        assert [item.path for item in call[1].files] == ["/etc/launch"]


async def test_linux_hosts_use_the_linux_child():
    linux, darwin = _both()
    runtime = _runtime(linux, darwin)
    bootstrap = runtime.machine_bootstrap(MachineBootstrap())
    for child in (linux, darwin):
        child.calls.clear()

    await runtime.start(_lease(LINUX), bootstrap)

    assert [call[0] for call in linux.calls] == ["start"]
    assert darwin.calls == []


async def test_an_unencoded_bootstrap_is_passed_through_unchanged():
    linux = FakeRuntime(LINUX)
    runtime = _runtime(linux=linux)
    bootstrap = MachineBootstrap(files=(BootstrapFile(path="/etc/x", content="x"),))

    await runtime.start(_lease(LINUX), bootstrap)

    assert linux.calls == [("start", bootstrap)]


async def test_a_refused_platform_fails_the_session_with_the_childs_reason():
    linux = FakeRuntime(LINUX)
    darwin = FakeRuntime(DARWIN, refuse="no container image on a Mac")
    runtime = _runtime(linux, darwin)
    bootstrap = runtime.machine_bootstrap(MachineBootstrap())
    lease = _lease(DARWIN)

    for call in (
        runtime.warm(lease.allocation, bootstrap),
        runtime.prepare(lease, bootstrap),
        runtime.start(lease, bootstrap),
        runtime.target(lease, bootstrap),
        runtime.ready(lease, bootstrap),
    ):
        with pytest.raises(ValueError, match="cannot run on this darwin host: no container"):
            await call

    assert darwin.calls == [("machine_bootstrap", MachineBootstrap())]


async def test_stopping_on_a_refused_platform_reaches_the_child():
    darwin = FakeRuntime(DARWIN, refuse="no")
    runtime = _runtime(FakeRuntime(LINUX), darwin)
    bootstrap = runtime.machine_bootstrap(MachineBootstrap())

    await runtime.stop(_lease(DARWIN), bootstrap)
    await runtime.recycle(_lease(DARWIN), bootstrap)

    assert [call[0] for call in darwin.calls[-2:]] == ["stop", "recycle"]


async def test_a_platform_without_a_runtime_raises():
    runtime = _runtime(linux=FakeRuntime(LINUX))

    with pytest.raises(ValueError, match="runs no sessions on darwin hosts"):
        await runtime.start(_lease(DARWIN), MachineBootstrap())
    with pytest.raises(ValueError, match="runs no sessions on darwin hosts"):
        await runtime.stop(_lease(DARWIN), MachineBootstrap())


@pytest.mark.parametrize("with_machine", [True, False])
async def test_a_lease_with_no_recorded_platform_raises(with_machine):
    runtime = _runtime(*_both())
    lease = _lease(None, with_machine=with_machine)

    with pytest.raises(ValueError, match="no host platform recorded"):
        await runtime.start(lease, MachineBootstrap())
    with pytest.raises(ValueError, match="no host platform recorded"):
        await runtime.recycle(lease, MachineBootstrap())
