"""The placement pod manager keeps local sessions local and pool sessions in the pool."""

from __future__ import annotations

from uuid import uuid4

import pytest

from niuu.ports.session_proxy import SessionProxyTarget
from tests.conftest import MockPodManager
from volundr.adapters.outbound.placement_pod_manager import PlacementPodManager
from volundr.domain.models import Session, SessionStatus
from volundr.domain.ports import HostedUnitError, SessionCapacity
from volundr.domain.session_runtimes import (
    ReadinessState,
    RuntimeReadiness,
    SessionRuntimeKind,
    SessionRuntimeOffer,
)
from volundr.domain.session_startup import StartupStage, StartupStageState

PIN = {"execution_id": "host", "revision": "1", "plan_digest": "digest"}
TARGET = SessionProxyTarget(
    service_url="http://10.0.0.5:8443", connect_host="10.0.0.5", connect_port=8443
)


class LocalManager(MockPodManager):
    """A local manager with the hooks and answers a LocalProcessPodManager has."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.registries: list[object] = []
        self.closed = False

    @property
    def runtime_backend(self) -> str:
        return "process"

    @property
    def delivers_forge_session_token(self) -> bool:
        return True

    async def capacity(self) -> SessionCapacity:
        return SessionCapacity(limit=4, active=1, remedy="raise pod_manager.max_concurrent")

    def initial_chat_endpoint(self, session: Session) -> str:
        return "ws://127.0.0.1:9100/session"

    def set_skuld_registry(self, registry: object) -> None:
        self.registries.append(registry)

    async def close(self) -> None:
        self.closed = True


class PoolManager(MockPodManager):
    """A compute pool manager with the surface a VmPodManager has."""

    def __init__(self, *, delivers: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.delivers = delivers
        self.registries: list[object] = []
        self.issuers: list[object] = []
        self.configured: list[tuple] = []
        self.resolved: list[Session] = []
        self.closed = False

    @property
    def runtime_backend(self) -> str:
        return "vm"

    @property
    def delivers_forge_session_token(self) -> bool:
        return self.delivers

    @property
    def profile(self) -> str:
        return "host"

    @property
    def reports_startup_stages(self) -> bool:
        return True

    async def startup_stages(self, session: Session) -> tuple[StartupStage, ...]:
        return (StartupStage(key="claim", label="Claim a host", state=StartupStageState.PENDING),)

    async def capacity(self) -> SessionCapacity:
        return SessionCapacity(limit=2, active=2, remedy="add a host")

    def initial_chat_endpoint(self, session: Session) -> str:
        return f"ws://forge/s/{session.id}/session"

    async def session_runtimes(self) -> tuple[SessionRuntimeOffer, ...]:
        return (
            SessionRuntimeOffer(
                runtime=SessionRuntimeKind.HOST,
                execution_profile="host@1",
                default=True,
                grants=(),
                readiness=RuntimeReadiness(state=ReadinessState.WARM, warm=1),
            ),
        )

    async def session_proxy_target(self, session: Session) -> SessionProxyTarget:
        return TARGET

    async def broker_target(self, session: Session) -> SessionProxyTarget:
        return TARGET

    def configure_compute(self, *args, **kwargs) -> None:
        self.configured.append(("compute", args, kwargs))

    def configure_execution(self, *args, **kwargs) -> None:
        self.configured.append(("execution", args, kwargs))

    def configure_pool(self, service) -> None:
        self.configured.append(("pool", (service,), {}))

    def set_skuld_registry(self, registry: object) -> None:
        self.registries.append(registry)

    def set_workload_token_issuer(self, issuer: object) -> None:
        self.issuers.append(issuer)

    async def has_legacy_allocation(self, session: Session) -> bool:
        return False

    async def resolve_execution(self, session: Session):
        self.resolved.append(session)
        return "plan"

    async def execution_for(self, session: Session):
        return "pinned-plan"

    def execution_reference(self, plan) -> dict[str, str]:
        return dict(PIN)

    async def close(self) -> None:
        self.closed = True


def _session(**workload_config) -> Session:
    return Session(
        id=uuid4(),
        name="placed",
        owner_id="owner",
        tenant_id="tenant",
        workload_config=workload_config,
    )


def _placement(**pool_kwargs) -> tuple[PlacementPodManager, LocalManager, PoolManager]:
    local, pool = LocalManager(), PoolManager(**pool_kwargs)
    return PlacementPodManager(local=local, pool=pool), local, pool


@pytest.mark.parametrize(
    "workload_config",
    [
        {"_compute_execution": PIN},
        {"host_selector": {"tags": ["gpu"], "match": "all"}},
        {"host_selector": {"tags": []}},
        {"execution_profile": "host@1"},
        {"compute_profile": "bm-host"},
        {"runtime_grants": ["sudo"]},
    ],
)
async def test_a_session_that_asks_for_the_pool_runs_there_and_never_locally(workload_config):
    placement, local, pool = _placement()
    session = _session(**workload_config)

    assert placement.runs_on_compute(session)
    await placement.start(session, None)
    assert await placement.stop(session)
    assert await placement.status(session) == session.status

    assert [call[0] for call in pool.start_calls] == [session]
    assert pool.stop_calls == [session]
    assert local.start_calls == []
    assert local.stop_calls == []
    assert placement.initial_chat_endpoint(session) == f"ws://forge/s/{session.id}/session"
    assert await placement.session_proxy_target(session) == TARGET
    assert await placement.broker_target(session) == TARGET


@pytest.mark.parametrize(
    "workload_config",
    [{}, {"persona": "coder"}, {"execution_profile": ""}, {"runtime_grants": []}],
)
async def test_a_session_that_asks_for_nothing_stays_local(workload_config):
    placement, local, pool = _placement()
    session = _session(**workload_config)

    assert not placement.runs_on_compute(session)
    await placement.start(session, None)
    assert await placement.destroy(session)

    assert [call[0] for call in local.start_calls] == [session]
    assert local.stop_calls == [session]
    assert pool.start_calls == []
    assert pool.stop_calls == []
    assert placement.initial_chat_endpoint(session) == "ws://127.0.0.1:9100/session"
    assert placement.initial_code_endpoint(session) is None
    assert await placement.session_proxy_target(session) is None
    assert await placement.broker_target(session) is None
    assert await placement.status_detail(session) is None


async def test_a_pool_start_failure_is_raised_and_never_retried_locally():
    local, pool = LocalManager(), PoolManager(start_success=False)
    placement = PlacementPodManager(local=local, pool=pool)

    with pytest.raises(RuntimeError, match="Pod start failed"):
        await placement.start(_session(host_selector={"tags": ["gpu"]}), None)
    assert local.start_calls == []


async def test_a_local_start_failure_is_raised_and_never_sent_to_the_pool():
    local, pool = LocalManager(start_success=False), PoolManager()
    placement = PlacementPodManager(local=local, pool=pool)

    with pytest.raises(RuntimeError, match="Pod start failed"):
        await placement.start(_session(), None)
    assert pool.start_calls == []


async def test_execution_is_resolved_only_for_pool_sessions():
    placement, _, pool = _placement()
    local_session = _session()
    pool_session = _session(execution_profile="host@1")

    assert await placement.resolve_execution(local_session) is None
    assert await placement.resolve_execution(pool_session) == "plan"
    assert pool.resolved == [pool_session]
    assert await placement.execution_for(pool_session) == "pinned-plan"
    assert placement.execution_reference("plan") == PIN
    assert await placement.has_legacy_allocation(local_session) is False


async def test_whole_forge_answers_come_from_the_local_default():
    placement, _, _ = _placement()

    assert placement.runtime_backend == "process"
    assert (await placement.capacity()).limit == 4
    # The pool's offers would make a launch form send every launch to a host.
    assert await placement.session_runtimes() == ()
    assert (await placement.capacity_for(_session(host_selector={"tags": []}))).limit == 2
    assert (await placement.capacity_for(_session())).limit == 4


def test_a_session_credential_is_minted_only_when_both_deliver_it():
    assert _placement()[0].delivers_forge_session_token
    assert not _placement(delivers=False)[0].delivers_forge_session_token


def test_a_session_credential_follows_the_manager_that_runs_the_session():
    """A pool that delivers nothing (no runtime configured) does not starve local sessions."""
    placement, _, _ = _placement(delivers=False)

    assert placement.delivers_forge_session_token_for(_session())
    assert not placement.delivers_forge_session_token_for(_session(host_selector={"tags": []}))


async def test_startup_stages_come_from_the_child_that_runs_the_session():
    placement, _, _ = _placement()

    assert placement.reports_startup_stages
    assert await placement.startup_stages(_session()) is None
    stages = await placement.startup_stages(_session(execution_profile="host@1"))
    assert [stage.key for stage in stages] == ["claim"]


async def test_wait_for_ready_follows_the_session():
    placement, local, pool = _placement()

    await placement.wait_for_ready(_session(), 5)
    await placement.wait_for_ready(_session(compute_profile="bm-host"), 7)

    assert [timeout for _, timeout in local.wait_for_ready_calls] == [5]
    assert [timeout for _, timeout in pool.wait_for_ready_calls] == [7]


def test_set_hooks_reach_every_child_that_has_them_and_no_other():
    placement, local, pool = _placement()
    registry, issuer = object(), object()

    placement.set_skuld_registry(registry)
    placement.set_workload_token_issuer(issuer)

    assert local.registries == [registry] and pool.registries == [registry]
    assert pool.issuers == [issuer]
    # A hook no child has is absent, so hasattr-based composition skips it.
    assert not hasattr(placement, "set_persona_registry")
    assert not hasattr(placement, "configure_everything")


def test_compute_composition_goes_to_the_pool():
    placement, _, pool = _placement()
    service = object()

    placement.configure_compute("svc", "repo", owns_runtime=False)
    placement.configure_execution("resolver", {}, {})
    placement.configure_pool(service)

    assert pool.configured == [
        ("compute", ("svc", "repo"), {"owns_runtime": False}),
        ("execution", ("resolver", {}, {}), {}),
        ("pool", (service,), {}),
    ]
    assert placement.profile == "host"


async def test_close_closes_both_children():
    placement, local, pool = _placement()

    await placement.close()

    assert local.closed and pool.closed


def test_local_and_pool_must_be_distinct_pod_managers_and_the_pool_a_compute_pool():
    pool = PoolManager()
    with pytest.raises(TypeError, match="must implement PodManager"):
        PlacementPodManager(local=object(), pool=pool)
    with pytest.raises(ValueError, match="must be different managers"):
        PlacementPodManager(local=pool, pool=pool)
    with pytest.raises(TypeError, match="has no configure_compute"):
        PlacementPodManager(local=LocalManager(), pool=LocalManager())


def test_unknown_attributes_are_not_invented():
    placement, _, _ = _placement()

    with pytest.raises(AttributeError):
        placement.max_concurrent  # noqa: B018


async def test_a_running_pool_session_reports_its_pool_status():
    placement, _, _ = _placement()
    session = _session(_compute_execution=PIN).model_copy(update={"status": SessionStatus.RUNNING})

    assert await placement.status(session) == SessionStatus.RUNNING


async def _member_source(unit_id, member_id):
    return []


def test_the_member_source_reaches_both_managers():
    placement, local, pool = _placement()

    placement.configure_hosted_members(_member_source)

    assert local._hosted_members is _member_source
    assert pool._hosted_members is _member_source


async def test_a_members_restart_and_stop_go_to_the_manager_that_runs_its_unit():
    placement, local, pool = _placement()
    calls = []

    async def restart(member):
        calls.append(("pool", member.id))

    async def stop_member(member):
        calls.append(("pool-stop", member.id))

    pool.restart_hosted_member = restart
    pool.stop_hosted_member = stop_member
    member = Session(
        id=uuid4(), name="m", hosted_by=uuid4(), hosted_port=8082,
        workload_config={"_compute_execution": PIN},
    )  # fmt: skip
    local_member = Session(id=uuid4(), name="l", hosted_by=uuid4(), hosted_port=8082)

    await placement.restart_hosted_member(member)
    await placement.stop_hosted_member(member)
    with pytest.raises(HostedUnitError, match="cannot start one member"):
        await placement.restart_hosted_member(local_member)

    assert calls == [("pool", member.id), ("pool-stop", member.id)]
