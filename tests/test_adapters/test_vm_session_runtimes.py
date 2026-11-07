"""A VM Forge offers the runtimes its catalog lists for launch, with warm readiness."""

from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from tests.test_adapters.test_file_execution_catalog import _yaml
from tests.test_adapters.test_vm_execution_lifecycle import (
    RecordingPlanRuntime,
    RecordingPreparation,
    _resolver,
)
from tests.test_adapters.test_vm_pod_manager import ReadyProvider, Repository
from volundr.adapters.outbound.file_execution_catalog import FileExecutionCatalog
from volundr.adapters.outbound.ssh_openshell_vm_runtime import SshOpenShellVmRuntime
from volundr.adapters.outbound.ssh_vm_runtime import SshContainerVmRuntime
from volundr.adapters.outbound.vm_pod_manager import VmPodManager
from volundr.domain.compute import ComputePoolPolicy, MachineBootstrap
from volundr.domain.execution_catalog import ExecutionCatalogError, ExecutionSelectionError
from volundr.domain.models import PodSpecAdditions, Session, SessionSpec
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.services.compute_pool import ComputePoolService
from volundr.domain.services.execution_catalog import ExecutionPlanResolver
from volundr.domain.session_runtimes import (
    ReadinessState,
    SessionGrant,
    SessionRuntimeKind,
    requested_grants,
)
from volundr.domain.vm_runtime import VmRuntime


class KindRuntime(RecordingPlanRuntime):
    def __init__(self, name: str, kind: SessionRuntimeKind | None, grants=()) -> None:
        super().__init__(name)
        self._kind = kind
        self._grants = tuple(grants)

    @property
    def session_runtime(self):
        return self._kind

    @property
    def session_grants(self):
        return self._grants


def _catalog(tmp_path: Path, launch: list[str] | None) -> ExecutionPlanResolver:
    """The lifecycle catalog plus a second execution on the same host, ``vm-b``."""
    resolver = _resolver(tmp_path)
    execution = yaml.safe_load((tmp_path / "executions" / "vm.yaml").read_text(encoding="utf-8"))
    _yaml(tmp_path / "executions" / "vm-b.yaml", {**execution, "id": "vm-b"})
    catalog_path = tmp_path / "catalog.yaml"
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    if launch is not None:
        catalog["launch_executions"] = [
            {"id": entry, "revision": execution["revision"]} for entry in launch
        ]
    _yaml(catalog_path, catalog)
    return resolver


def _manager(resolver: ExecutionPlanResolver, runtimes: dict[str, VmRuntime], *, max_machines=2):
    plans = {plan.execution.id: plan for plan in resolver.plans()}
    repository = Repository()
    provider = ReadyProvider()
    leases = ComputeLeaseService(
        repository,
        provider,
        pool_id="pool",
        max_machines=max_machines,
        bootstrap=MachineBootstrap(),
        bootstrap_store=MemoryCredentialStore(),
        provider_binding="provider",
        provider_fingerprint="provider-config-v1",
    )
    manager = VmPodManager(
        profile="small", pool_id="pool", max_machines=max_machines, poll_interval_seconds=0.001
    )
    manager.configure_compute(
        leases,
        repository,
        RecordingPlanRuntime("legacy"),
        MachineBootstrap(),
        pool_id="pool",
        max_machines=max_machines,
    )
    by_digest = {plans[name].plan_digest: runtime for name, runtime in runtimes.items()}
    manager.configure_execution(
        resolver,
        by_digest,
        {plan.plan_digest: RecordingPreparation() for plan in plans.values()},
    )
    return manager, leases, repository, provider, plans


def _session(manager: VmPodManager, plan, **config) -> Session:
    return Session(
        id=uuid4(),
        name="runtime choice",
        owner_id="owner",
        tenant_id="tenant",
        workload_config={"execution_profile": str(plan.execution), **config},
    )


async def test_a_catalog_without_launch_executions_offers_no_choice(tmp_path: Path):
    resolver = _catalog(tmp_path, None)
    manager, *_ = _manager(
        resolver,
        {
            "vm": KindRuntime("vm", SessionRuntimeKind.CONTAINER),
            "vm-b": KindRuntime("vm-b", None),
        },
    )

    assert await manager.session_runtimes() == ()


async def test_a_pod_manager_without_a_catalog_offers_no_choice():
    manager = VmPodManager(profile="small", pool_id="pool", max_machines=1)

    assert await manager.session_runtimes() == ()


async def test_offers_follow_the_catalog_order_and_mark_the_default(tmp_path: Path):
    resolver = _catalog(tmp_path, ["vm-b", "vm"])
    manager, *_ = _manager(
        resolver,
        {
            "vm": KindRuntime("vm", SessionRuntimeKind.CONTAINER),
            "vm-b": KindRuntime("vm-b", SessionRuntimeKind.OPENSHELL, [SessionGrant.SUDO]),
        },
    )

    offers = await manager.session_runtimes()

    assert [(o.runtime, o.execution_profile, o.default) for o in offers] == [
        (SessionRuntimeKind.OPENSHELL, "vm-b@2026-09-18", False),
        (SessionRuntimeKind.CONTAINER, "vm@2026-09-18", True),
    ]
    assert offers[0].grants == (SessionGrant.SUDO,)
    assert offers[1].grants == ()
    assert {o.readiness.state for o in offers} == {ReadinessState.BOOT}


async def test_a_free_prepared_machine_makes_each_runtime_on_its_host_warm(tmp_path: Path):
    resolver = _catalog(tmp_path, ["vm", "vm-b"])
    manager, leases, repository, provider, plans = _manager(
        resolver,
        {
            "vm": KindRuntime("vm", SessionRuntimeKind.CONTAINER),
            "vm-b": KindRuntime("vm-b", SessionRuntimeKind.OPENSHELL),
        },
    )
    pool = ComputePoolService(
        repository,
        leases,
        provider,
        RecordingPlanRuntime("legacy-pool"),
        pool_id="pool",
        defaults=ComputePoolPolicy(profile="small", max_machines=2, warm_min=1),
        bootstrap=MachineBootstrap(),
        interval_seconds=0.001,
    )
    pool.configure_execution(
        resolver,
        {plan.plan_digest: KindRuntime("pool", None) for plan in plans.values()},
        {plan.plan_digest: RecordingPreparation() for plan in plans.values()},
    )
    manager.configure_pool(pool)
    await pool.maintain()
    await pool.maintain()

    offers = await manager.session_runtimes()

    assert [(o.readiness.state, o.readiness.warm) for o in offers] == [
        (ReadinessState.WARM, 1),
        (ReadinessState.WARM, 1),
    ]

    # A session takes the spare's only slot: it is in service, not warm.
    plan = plans["vm"]
    session = _session(manager, plan, _compute_execution=manager.execution_reference(plan))
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    assert len(repository.leases) == 1

    offers = await manager.session_runtimes()

    assert [(o.readiness.state, o.readiness.warm) for o in offers] == [
        (ReadinessState.BOOT, 0),
        (ReadinessState.BOOT, 0),
    ]


async def test_a_full_pool_names_where_capacity_is_raised(tmp_path: Path):
    resolver = _catalog(tmp_path, ["vm"])
    manager, _, _, _, plans = _manager(
        resolver,
        {
            "vm": KindRuntime("vm", SessionRuntimeKind.CONTAINER),
            "vm-b": KindRuntime("vm-b", None),
        },
        max_machines=1,
    )
    plan = plans["vm"]
    session = _session(manager, plan, _compute_execution=manager.execution_reference(plan))
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))

    [offer] = await manager.session_runtimes()

    assert offer.readiness.state is ReadinessState.FULL
    assert offer.readiness.warm == 0
    assert "pool capacity" in offer.readiness.remedy


async def test_an_offered_execution_whose_runtime_has_no_kind_fails_composition(tmp_path: Path):
    resolver = _catalog(tmp_path, ["vm", "vm-b"])
    with pytest.raises(ValueError, match="cannot be offered as a launch choice"):
        _manager(
            resolver,
            {
                "vm": KindRuntime("vm", SessionRuntimeKind.CONTAINER),
                "vm-b": KindRuntime("vm-b", None),
            },
        )


async def test_two_offered_executions_of_one_kind_fail_composition(tmp_path: Path):
    resolver = _catalog(tmp_path, ["vm", "vm-b"])
    with pytest.raises(ValueError, match="one execution per runtime"):
        _manager(
            resolver,
            {
                "vm": KindRuntime("vm", SessionRuntimeKind.CONTAINER),
                "vm-b": KindRuntime("vm-b", SessionRuntimeKind.CONTAINER),
            },
        )


async def test_grants_the_selected_runtime_does_not_offer_are_refused(tmp_path: Path):
    resolver = _catalog(tmp_path, ["vm", "vm-b"])
    manager, _, _, _, plans = _manager(
        resolver,
        {
            "vm": KindRuntime("vm", SessionRuntimeKind.CONTAINER),
            "vm-b": KindRuntime("vm-b", SessionRuntimeKind.HOST, [SessionGrant.SUDO]),
        },
    )

    with pytest.raises(ExecutionSelectionError, match=r"does not offer runtime_grants \['sudo'\]"):
        await manager.resolve_execution(_session(manager, plans["vm"], runtime_grants=["sudo"]))
    with pytest.raises(ExecutionSelectionError, match=r"\['docker'\]"):
        await manager.resolve_execution(
            _session(manager, plans["vm-b"], runtime_grants=["sudo", "docker"])
        )
    plan = await manager.resolve_execution(
        _session(manager, plans["vm-b"], runtime_grants=["sudo"])
    )
    assert plan.execution.id == "vm-b"


async def test_malformed_grants_are_a_selection_error(tmp_path: Path):
    resolver = _catalog(tmp_path, None)
    manager, _, _, _, plans = _manager(
        resolver,
        {"vm": KindRuntime("vm", None), "vm-b": KindRuntime("vm-b", None)},
    )

    with pytest.raises(ExecutionSelectionError, match="list of grant names"):
        await manager.resolve_execution(_session(manager, plans["vm"], runtime_grants="sudo"))
    with pytest.raises(ExecutionSelectionError, match="known grants: sudo, docker"):
        await manager.resolve_execution(_session(manager, plans["vm"], runtime_grants=["root"]))


async def test_grants_on_a_legacy_allocation_are_refused(tmp_path: Path):
    resolver = _catalog(tmp_path, None)
    manager, leases, _, _, _ = _manager(
        resolver,
        {"vm": KindRuntime("vm", None), "vm-b": KindRuntime("vm-b", None)},
    )
    session = Session(
        id=uuid4(),
        name="legacy",
        owner_id="owner",
        tenant_id="tenant",
        workload_config={"runtime_grants": ["docker"]},
    )
    await leases.acquire(
        session_id=session.id,
        owner_id="owner",
        tenant_id="tenant",
        profile="small",
        bootstrap=MachineBootstrap(),
    )

    with pytest.raises(ExecutionSelectionError, match="does not offer"):
        await manager.resolve_execution(session)


def test_requested_grants_reads_absent_as_none():
    assert requested_grants({}) == frozenset()
    assert requested_grants({"runtime_grants": ["docker"]}) == {SessionGrant.DOCKER}


def test_unknown_or_repeated_launch_executions_are_rejected(tmp_path: Path):
    _catalog(tmp_path, ["missing"])
    with pytest.raises(ExecutionCatalogError, match="Launch execution does not exist"):
        FileExecutionCatalog(tmp_path).load()

    _catalog(tmp_path / "again", ["vm", "vm"])
    with pytest.raises(ValueError, match="Launch executions must be unique"):
        FileExecutionCatalog(tmp_path / "again").load()


def test_vm_runtime_adapters_declare_what_sessions_run_in():
    assert SshContainerVmRuntime.session_runtime.fget(None) is SessionRuntimeKind.CONTAINER
    assert SshOpenShellVmRuntime.session_runtime.fget(None) is SessionRuntimeKind.OPENSHELL
    assert VmRuntime.session_runtime.fget(None) is None
    assert VmRuntime.session_grants.fget(None) == ()


def test_the_openshell_gateway_runtime_runs_sessions_in_openshell():
    pytest.importorskip("openshell")
    from volundr.adapters.outbound.openshell_vm_runtime import OpenShellVmRuntime

    assert OpenShellVmRuntime.session_runtime.fget(None) is SessionRuntimeKind.OPENSHELL
